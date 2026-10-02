"""Provider-backed semantic indexing and pgvector retrieval for restaurant knowledge."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
from array import array
from collections import OrderedDict

import httpx
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Iterable, Optional

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from jubran.application.ai.content_state import mark_indexed, read_versions
from jubran.application.ai.model_config_service import ModelConfigService, default_model, default_provider
from jubran.application.ai.text_match import contains_name, name_matches
from jubran.application.ai.provider_errors import classify_provider_failure
from jubran.infrastructure.db.models import (
    EMBEDDING_DIMENSIONS,
    BranchModel,
    KnowledgeDocumentModel,
    MenuCategoryModel,
    ProductModel,
    RestaurantModel,
)
from jubran.infrastructure.db.transactions import end_transaction
from jubran.settings import settings
from jubran.domain.money import format_jod

logger = logging.getLogger(__name__)


class SemanticRetrievalError(Exception):
    """A stable failure raised when semantic retrieval cannot run."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SourceDocument:
    source_type: str
    source_id: str
    title: str
    content: str
    metadata: dict[str, Any]

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EmbeddingTarget:
    provider: str
    model: str

    @property
    def label(self) -> str:
        """Stored with every indexed document; a different label means re-embedding."""
        return self.model if self.provider == "gemini" else f"{self.provider}:{self.model}"


GEMINI_BATCH_LIMIT = 100  # texts per Gemini embedding request (the API's own limit)
GEMINI_RATE_LIMIT_RETRIES = 3  # indexing waits this many times for a per-minute rate limit


async def _embed_batch(client: Any, model: str, batch: list[str], config: Any, retries: int) -> Any:
    """One Gemini embedding request; can wait out a per-minute rate limit (never a daily one)."""
    from google.genai import types

    for attempt in range(retries + 1):
        try:
            return await client.aio.models.embed_content(
                model=model,
                contents=[types.Content(parts=[types.Part(text=item)]) for item in batch],
                config=config,
            )
        except Exception as exc:
            message = str(exc)
            if attempt == retries or "429" not in message or "PerDay" in message:
                raise
            delay = re.search(r"retryDelay['\"]?:\s*['\"](\d+(?:\.\d+)?)s", message)
            wait = min(float(delay.group(1)) + 1 if delay else 30.0, 65.0)
            logger.info("Embedding rate limit reached; retrying in %.0f s", wait)
            await asyncio.sleep(wait)


class EmbeddingService:
    """Embeddings for documents and questions, from Gemini or OpenAI (independent of the chat model)."""

    # The same text always gets the same vector, so recent ones are reused: guests ask
    # for the same dishes all day, and every repeat saves a provider call and quota.
    CACHE_SIZE = 2000
    MAX_TEXTS_PER_REQUEST = 100
    _cache: "OrderedDict[tuple[str, str, str], array]" = OrderedDict()

    @staticmethod
    async def target(db: AsyncSession) -> EmbeddingTarget:
        """Menu search's provider and model: its admin settings section, else the server default."""
        model = await ModelConfigService.in_use(db, "embedding")
        if model is not None:
            return EmbeddingTarget(model.provider, model.model_id)
        provider = default_provider("embedding") or "gemini"
        return EmbeddingTarget(provider, default_model("embedding", provider))

    @staticmethod
    async def _api_key(db: AsyncSession) -> str:
        try:
            return (await ModelConfigService.runtime(db, "embedding")).api_key
        except ValueError as exc:
            raise SemanticRetrievalError("EMBEDDING_PROVIDER_NOT_CONFIGURED") from exc

    @classmethod
    async def provider_vectors(cls, provider: str, api_key: str, model: str, texts: list[str],
                               task_type: str) -> list[list[float]]:
        """Vectors straight from the provider (no cache), in the size the search index uses."""
        vectors: list[list[float]] = []
        try:
            # Providers cap how many texts one request may carry (Gemini: 100), and a full
            # menu has more documents than that, so it is sent in batches.
            for start in range(0, len(texts), cls.MAX_TEXTS_PER_REQUEST):
                batch = texts[start:start + cls.MAX_TEXTS_PER_REQUEST]
                if provider == "openai":
                    vectors += await cls._openai_vectors(api_key, model, batch)
                else:
                    vectors += await cls._gemini_vectors(api_key, model, batch, task_type)
        except Exception as exc:
            raise SemanticRetrievalError(classify_provider_failure(exc, "embedding")) from exc
        if len(vectors) != len(texts) or any(len(vector) != EMBEDDING_DIMENSIONS for vector in vectors):
            raise SemanticRetrievalError("INVALID_EMBEDDING_RESPONSE")
        return vectors

    @classmethod
    async def embed(cls, db: AsyncSession, texts: list[str], *, task_type: str) -> list[list[float]]:
        if not texts:
            return []
        target = await cls.target(db)
        keys = [(target.label, task_type, text) for text in texts]
        missing = list(dict.fromkeys(key for key in keys if key not in cls._cache))
        if missing:
            vectors = await cls.provider_vectors(target.provider, await cls._api_key(db), target.model,
                                                 [key[2] for key in missing], task_type)
            for key, vector in zip(missing, vectors):
                cls._cache[key] = array("f", vector)
        result = [list(cls._cache[key]) for key in keys]
        for key in keys:
            cls._cache.move_to_end(key)
        while len(cls._cache) > cls.CACHE_SIZE:
            cls._cache.popitem(last=False)
        return result

    @staticmethod
    async def _gemini_vectors(api_key: str, model: str, texts: list[str], task_type: str) -> list[list[float]]:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        config = types.EmbedContentConfig(task_type=task_type, output_dimensionality=EMBEDDING_DIMENSIONS)
        vectors: list[list[float]] = []
        try:
            # Gemini takes at most GEMINI_BATCH_LIMIT texts per request, and each text goes in
            # its own Content: newer models fold a plain list of strings into one vector.
            # Indexing many texts may wait out a per-minute limit; a guest's question never waits.
            retries = GEMINI_RATE_LIMIT_RETRIES if len(texts) > 1 else 0
            for start in range(0, len(texts), GEMINI_BATCH_LIMIT):
                batch = texts[start:start + GEMINI_BATCH_LIMIT]
                response = await _embed_batch(client, model, batch, config, retries)
                vectors.extend(list(item.values or []) for item in (response.embeddings or []))
            return vectors
        finally:
            await client.aio.aclose()
            client.close()

    @staticmethod
    async def _openai_vectors(api_key: str, model: str, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "input": texts, "dimensions": EMBEDDING_DIMENSIONS},
            )
            response.raise_for_status()
            data = sorted(response.json().get("data", []), key=lambda item: item.get("index", 0))
            return [list(item.get("embedding") or []) for item in data]


class SemanticKnowledgeService:
    ALLOWED_SOURCE_TYPES = {"product", "category", "restaurant", "branch", "service", "policy"}
    SOURCE_THRESHOLDS = {
        "category": 0.58,
        "restaurant": 0.52,
        "branch": 0.54,
        "service": 0.50,
        "policy": 0.50,
    }

    @staticmethod
    def _clean(parts: Iterable[Optional[str]]) -> str:
        return "\n".join(part.strip() for part in parts if part and part.strip())

    @classmethod
    async def _source_documents(cls, db: AsyncSession) -> list[SourceDocument]:
        documents: list[SourceDocument] = []

        restaurants = (await db.execute(select(RestaurantModel))).scalars().all()
        for restaurant in restaurants:
            documents.append(SourceDocument(
                "restaurant", restaurant.id, f"{restaurant.name_ar} | {restaurant.name_en}",
                cls._clean((
                    f"المطعم: {restaurant.name_ar}. Restaurant: {restaurant.name_en}.",
                    f"نبذة: {restaurant.about_ar}", f"About: {restaurant.about_en}",
                    f"هاتف التواصل الرسمي: {restaurant.phone}. Official phone: {restaurant.phone}.",
                )),
                {"restaurant_id": restaurant.id},
            ))

        branches = (await db.execute(
            select(BranchModel).options(selectinload(BranchModel.opening_hours))
        )).scalars().all()
        day_names_ar = ["الأحد", "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت"]
        for branch in branches:
            hours = sorted(branch.opening_hours, key=lambda item: item.day_of_week)
            hours_text = "; ".join(
                f"{day_names_ar[item.day_of_week % 7]}: {item.opens_at}-{item.closes_at}"
                + (f" ({item.notes_ar} / {item.notes_en})" if item.notes_ar or item.notes_en else "")
                for item in hours
            )
            documents.append(SourceDocument(
                "branch", branch.id, f"{branch.name_ar} | {branch.name_en}",
                cls._clean((
                    f"الفرع: {branch.name_ar}. Branch: {branch.name_en}.",
                    # The branch with the tables is the one the guests are sitting in.
                    "هذا هو الفرع الذي يجلس فيه الزبون الآن. This is the branch the guest is sitting in."
                    if branch.is_demo_branch else "فرع آخر للمطعم. Another branch of the restaurant.",
                    f"العنوان: {branch.address_ar}. Address: {branch.address_en}.",
                    f"هاتف الفرع: {branch.phone}. Branch phone: {branch.phone}.",
                    f"أوقات الدوام: {hours_text}. Opening hours: {hours_text}.",
                )),
                {"branch_id": branch.id, "restaurant_id": branch.restaurant_id},
            ))

        categories = (await db.execute(
            select(MenuCategoryModel).options(selectinload(MenuCategoryModel.products))
        )).scalars().all()
        category_names = {category.id: (category.name_ar, category.name_en) for category in categories}
        for category in categories:
            product_names_ar = "، ".join(product.name_ar for product in category.products)
            product_names_en = ", ".join(product.name_en for product in category.products)
            documents.append(SourceDocument(
                "category", category.id, f"{category.name_ar} | {category.name_en}",
                cls._clean((
                    f"تصنيف منيو: {category.name_ar}. Menu category: {category.name_en}.",
                    f"الأصناف التابعة له: {product_names_ar}.",
                    f"Products in this category: {product_names_en}.",
                )),
                {"category_id": category.id, "is_active": category.is_active,
                 "sort_order": category.sort_order},
            ))

        products = (await db.execute(select(ProductModel))).scalars().all()
        for product in products:
            category_ar, category_en = category_names.get(product.category_id, ("", ""))
            documents.append(SourceDocument(
                "product", product.id, f"{product.name_ar} | {product.name_en}",
                cls._clean((
                    f"منتج من المنيو: {product.name_ar}. Menu product: {product.name_en}.",
                    f"التصنيف: {category_ar}. Category: {category_en}.",
                    f"الوصف العربي: {product.description_ar or 'لا يوجد وصف موثق'}.",
                    f"English description: {product.description_en or 'No verified description'}.",
                )),
                {"product_id": product.id, "category_id": product.category_id},
            ))

        service_documents = (
            ("staff", "طلب موظف | Call staff", "يمكن للزبون طلب موظف أو نادل أو شخص يساعده على الطاولة. The customer can call a staff member or waiter for help."),
            ("tissues", "طلب مناديل | Request tissues", "يمكن للزبون طلب مناديل إضافية للطاولة. The customer can request extra tissues or napkins."),
            ("clean_table", "تنظيف الطاولة | Clean table", "يمكن للزبون طلب تنظيف أو مسح الطاولة. The customer can request table cleaning."),
            ("bill", "طلب الحساب | Request bill", "يمكن للزبون طلب الحساب أو الفاتورة للطاولة. The customer can request the bill or check."),
            ("complaint", "شكوى | Complaint", "يمكن للزبون تسجيل شكوى عن الطعام أو الخدمة أو النظافة أو التأخير من دون وعد بتعويض. Customers can report a complaint about food, service, cleanliness, or delay."),
        )
        for source_id, title, content in service_documents:
            documents.append(SourceDocument("service", source_id, title, content, {"capability": source_id}))

        documents.extend((
            SourceDocument(
                "policy", "allergens", "الحساسية الغذائية | Food allergens",
                "لا توجد بيانات حساسية أو مكونات موثقة إلا إذا ظهرت صراحة في بيانات المنتج. لا يجوز تأكيد أمان صنف لحساسية قوية، ويجب عرض طلب موظف للتأكد. Allergens are not verified unless explicitly present in product data; never guarantee safety for a severe allergy.",
                {"policy": "allergens"},
            ),
            SourceDocument(
                "policy", "ordering", "آلية الطلب | Ordering workflow",
                "تعديلات السلة تبقى مسودة ولا تُرسل للمطعم حتى يعرض المساعد الملخص والسعر الحالي ويؤكد الزبون في رسالة لاحقة. تعديل السلة يلغي التأكيد السابق. Draft changes are not submitted until a fresh summary is shown and explicitly confirmed in a later turn.",
                {"policy": "ordering"},
            ),
            SourceDocument(
                "policy", "privacy", "خصوصية الجلسة | Session privacy",
                "يستطيع الزبون رؤية طلبات وخدمات جلسة طاولته الحالية فقط. رقم الطاولة وهوية الجلسة يحددهما الخادم من رمز QR ولا يمكن تغييرهما برسالة. Customers may access only their current table session; server QR context owns identity and table number.",
                {"policy": "privacy"},
            ),
        ))
        return documents

    @classmethod
    async def ensure_fresh(cls, db: AsyncSession, force: bool = False) -> None:
        """Rebuild the index only when the menu/restaurant facts changed since it was built.

        Normally this is two tiny reads. The expensive work (and its lock) only
        happens right after an admin change, and once at startup (``force``).
        """
        content, indexed = await read_versions(db)
        # Switching the embedding provider/model also means re-embedding everything.
        label = (await EmbeddingService.target(db)).label
        other_model = (await db.execute(select(KnowledgeDocumentModel.id).where(
            KnowledgeDocumentModel.embedding_model != label).limit(1))).first() is not None
        await db.commit()  # never keep a read transaction open while embedding
        if not force and indexed >= content and not other_model:
            return
        await cls.sync(db)
        await mark_indexed(db, content)

    @staticmethod
    async def indexed_count(db: AsyncSession) -> int:
        """Documents searchable with the current embedding model (0 until the menu is indexed)."""
        label = (await EmbeddingService.target(db)).label
        return (await db.execute(select(func.count()).select_from(KnowledgeDocumentModel)
                                 .where(KnowledgeDocumentModel.embedding_model == label))).scalar_one()

    @classmethod
    async def refresh_after_admin_change(cls, db: AsyncSession) -> None:
        """Index an admin change right away; if the provider is down, the next search retries."""
        try:
            await cls.ensure_fresh(db)
        except Exception:
            await end_transaction(db)
            logger.warning("Assistant knowledge index refresh deferred", exc_info=True)

    @classmethod
    async def sync(cls, db: AsyncSession) -> None:
        # One indexer at a time across workers. The lock is released by the
        # commit at the end, which always happens, so it never outlives the sync.
        if db.bind and db.bind.dialect.name == "postgresql":
            await db.execute(text("SELECT pg_advisory_xact_lock(1642076131)"))

        label = (await EmbeddingService.target(db)).label
        sources = await cls._source_documents(db)
        source_map = {(item.source_type, item.source_id): item for item in sources}
        existing = (await db.execute(select(KnowledgeDocumentModel))).scalars().all()
        existing_map = {(item.source_type, item.source_id): item for item in existing}

        stale_sources = [
            source for key, source in source_map.items()
            if key not in existing_map
            or existing_map[key].source_fingerprint != source.fingerprint
            or existing_map[key].embedding_model != label
        ]
        stale_vectors = await EmbeddingService.embed(
            db, [source.content for source in stale_sources], task_type="RETRIEVAL_DOCUMENT"
        )
        for source, vector in zip(stale_sources, stale_vectors):
            key = (source.source_type, source.source_id)
            record = existing_map.get(key)
            if record is None:
                record = KnowledgeDocumentModel(source_type=source.source_type, source_id=source.source_id)
                db.add(record)
            record.title = source.title
            record.content = source.content
            record.metadata_json = source.metadata
            record.source_fingerprint = source.fingerprint
            record.embedding_model = label
            record.embedding = vector

        removed_ids = [record.id for key, record in existing_map.items() if key not in source_map]
        if removed_ids:
            await db.execute(delete(KnowledgeDocumentModel).where(KnowledgeDocumentModel.id.in_(removed_ids)))
        await db.commit()

    @classmethod
    def _name_hit_document(cls, product: ProductModel) -> SimpleNamespace:
        """A product found by its name, described from the live row (works even before indexing)."""
        return SimpleNamespace(
            source_type="product", source_id=product.id, embedding=None,
            title=f"{product.name_ar} | {product.name_en}",
            content=cls._clean((f"منتج من المنيو: {product.name_ar}. Menu product: {product.name_en}.",
                                product.description_ar, product.description_en)),
            metadata_json={"product_id": product.id, "category_id": product.category_id},
        )

    @staticmethod
    def _product_payload(product: ProductModel) -> dict[str, Any]:
        """A dish as search returns it, always from the live row (price and availability now)."""
        return {
            "id": product.id,
            "name_ar": product.name_ar,
            "name_en": product.name_en,
            "description_ar": product.description_ar,
            "description_en": product.description_en,
            "category_id": product.category_id,
            "price_minor": product.price_minor,
            "price_display_ar": format_jod(product.price_minor, "ar"),
            "price_display_en": format_jod(product.price_minor, "en"),
            "is_available": product.is_available,
            "image_asset_url": product.image_asset_url,
        }

    MAX_LISTED_PRODUCTS = 40

    @classmethod
    async def _listing(cls, db: AsyncSession, query: str, exclusions: list[str], requested_types: set[str],
                       available_only: bool) -> dict[str, Any]:
        """The restaurant's facts unranked, for when meaning search is unavailable.

        Dishes (in menu order) when dishes were asked for, otherwise the requested
        restaurant/branch/service/policy texts. Built from the live tables, so it works
        even if nothing was ever indexed.
        """
        matches: list[dict[str, Any]] = []
        if "product" in requested_types:
            stmt = (select(ProductModel)
                    .join(MenuCategoryModel, MenuCategoryModel.id == ProductModel.category_id)
                    .where(MenuCategoryModel.is_active.is_(True))
                    .order_by(MenuCategoryModel.sort_order, ProductModel.sort_order, ProductModel.name_ar)
                    .limit(cls.MAX_LISTED_PRODUCTS))
            if available_only:
                stmt = stmt.where(ProductModel.is_available.is_(True))
            for product in (await db.execute(stmt)).scalars().all():
                matches.append({"source_type": "product", "source_id": product.id,
                                "title": f"{product.name_ar} | {product.name_en}", "content": None, "metadata": {},
                                "similarity": None, "match_type": "listed", "product": cls._product_payload(product)})
        else:
            for source in await cls._source_documents(db):
                if source.source_type in requested_types:
                    matches.append({"source_type": source.source_type, "source_id": source.source_id,
                                    "title": source.title, "content": source.content, "metadata": source.metadata,
                                    "similarity": None, "match_type": "listed"})
        return {"success": True, "query": query, "excluded_meanings": exclusions, "matches": matches,
                "no_reliable_match": not matches, "retrieval": "listing", "embedding_model": None}

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        dot = sum(a * b for a, b in zip(left, right))
        norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
        return dot / norm if norm else 0.0

    @classmethod
    async def search(
        cls,
        db: AsyncSession,
        query: str,
        source_types: Optional[list[str]] = None,
        exclude_queries: Optional[list[str]] = None,
        limit: int = 10,
        available_only: bool = True,
        sort_by_price: bool = False,
        min_similarity: Optional[float] = None,
    ) -> dict[str, Any]:
        clean_query = (query or "").strip()
        if not clean_query:
            return {"success": False, "error_code": "EMPTY_SEMANTIC_QUERY", "matches": []}
        requested_types = set(source_types or cls.ALLOWED_SOURCE_TYPES)
        if not requested_types or not requested_types.issubset(cls.ALLOWED_SOURCE_TYPES):
            return {"success": False, "error_code": "INVALID_SOURCE_TYPES", "matches": []}
        limit = max(1, min(limit, 20))

        clean_exclusions = [item.strip() for item in (exclude_queries or []) if item and item.strip()][:10]
        semantic_failure: Optional[SemanticRetrievalError] = None
        query_vector: Optional[list[float]] = None
        exclusion_vectors: list[list[float]] = []
        try:
            await cls.ensure_fresh(db)
            query_vectors = await EmbeddingService.embed(
                db, [clean_query, *clean_exclusions], task_type="RETRIEVAL_QUERY"
            )
            query_vector, exclusion_vectors = query_vectors[0], query_vectors[1:]
        except SemanticRetrievalError as exc:
            # Embedding provider down or not configured: dishes named in the
            # question can still be found by name below.
            await end_transaction(db)
            logger.warning("Semantic search unavailable (%s); using name matching only", exc.code)
            semantic_failure = exc
        stmt = select(KnowledgeDocumentModel).where(KnowledgeDocumentModel.source_type.in_(requested_types))
        if available_only and "product" in requested_types:
            # Filter before ranking, so hidden dishes never use up the result slots.
            unavailable = select(ProductModel.id).where(ProductModel.is_available.is_(False))
            stmt = stmt.where(or_(KnowledgeDocumentModel.source_type != "product",
                                  KnowledgeDocumentModel.source_id.not_in(unavailable)))
        dialect = db.bind.dialect.name if db.bind else ""
        if query_vector is None:
            scored = []
        elif dialect == "postgresql":
            distance = KnowledgeDocumentModel.embedding.cosine_distance(query_vector)
            rows = (await db.execute(stmt.add_columns(distance.label("distance")).order_by(distance).limit(limit * 4))).all()
            scored = [(document, 1.0 - float(distance_value)) for document, distance_value in rows]
        else:
            documents = (await db.execute(stmt)).scalars().all()
            scored = sorted(
                ((document, cls._cosine_similarity(document.embedding, query_vector)) for document in documents),
                key=lambda item: item[1], reverse=True,
            )[:limit * 4]

        # Hybrid: dishes whose name is literally in the question always come first.
        name_hits: dict[str, Any] = {}
        kinds: list[list[str]] = []
        if "product" in requested_types:
            candidates = [product for product in (await db.execute(select(ProductModel))).scalars().all()
                          if not available_only or product.is_available]
            for product in candidates:
                if name_matches(clean_query, product.name_ar, product.name_en):
                    name_hits[product.id] = product
            # A named dish brings its variations along (the same name plus more words), so the
            # guest hears every kind even when meaning search ranks them low or is down.
            named = list(name_hits.values())
            variants = {product.id: product for product in candidates if product.id not in name_hits and any(
                contains_name(hit.name_ar, product.name_ar) or contains_name(hit.name_en, product.name_en)
                for hit in named)}
            for hit in named:  # each named dish with its variations: one kind, to be named in full
                members = [hit] + [product for product in variants.values()
                                   if contains_name(hit.name_ar, product.name_ar)
                                   or contains_name(hit.name_en, product.name_en)]
                if len(members) > 1:
                    kinds.append([product.name_ar for product in members])
            if name_hits:
                scored = ([(cls._name_hit_document(product), 1.0) for product in name_hits.values()]
                          + [(cls._name_hit_document(product), 0.99) for product in variants.values()]
                          + [(document, similarity) for document, similarity in scored
                             if not (document.source_type == "product"
                                     and (document.source_id in name_hits or document.source_id in variants))])
                name_hits.update(variants)
        if semantic_failure is not None and not name_hits:
            # Nothing was named and meaning search is down (provider outage, quota): the
            # guest's turn must not fail, so show what the restaurant has and let the model pick.
            return await cls._listing(db, clean_query, clean_exclusions, requested_types, available_only)

        product_ids = [document.source_id for document, _ in scored if document.source_type == "product"]
        products = {}
        if product_ids:
            products = {item.id: item for item in (await db.execute(
                select(ProductModel).where(ProductModel.id.in_(product_ids))
            )).scalars().all()}

        matches = []
        for document, similarity in scored:
            threshold = min_similarity if min_similarity is not None else (
                settings.SEMANTIC_MIN_SIMILARITY
                if document.source_type == "product"
                else cls.SOURCE_THRESHOLDS[document.source_type]
            )
            if similarity < threshold:
                continue
            if exclusion_vectors and document.embedding is not None and max(
                cls._cosine_similarity(list(document.embedding), exclusion) for exclusion in exclusion_vectors
            ) >= 0.71:
                continue
            match = {
                "source_type": document.source_type,
                "source_id": document.source_id,
                "title": document.title,
                "content": document.content,
                "metadata": document.metadata_json,
                "similarity": round(similarity, 4),
                "match_type": "name" if document.source_type == "product" and document.source_id in name_hits else "meaning",
            }
            if document.source_type == "product":
                product = products.get(document.source_id)
                if product is None or (available_only and not product.is_available):
                    continue
                match["product"] = cls._product_payload(product)
            matches.append(match)

        matches.sort(key=lambda item: item["similarity"], reverse=True)

        if sort_by_price:
            product_matches = [item for item in matches if item.get("product")]
            other_matches = [item for item in matches if not item.get("product")]
            product_matches.sort(key=lambda item: (item["product"]["price_minor"], -item["similarity"]))
            matches = product_matches + other_matches

        # Dishes found by name (and their variations) are never cut by the limit: a kind is shown whole.
        room = max(limit - sum(1 for match in matches if match["match_type"] == "name"), 0)
        kept = []
        for match in matches:
            if match["match_type"] == "name":
                kept.append(match)
            elif room > 0:
                kept.append(match)
                room -= 1
        matches = kept
        return {
            "success": True,
            "query": clean_query,
            "excluded_meanings": clean_exclusions,
            "matches": matches,
            "kinds": kinds,
            "no_reliable_match": not matches,
            "retrieval": "name_only" if semantic_failure else "name_and_semantic",
            "embedding_model": None if semantic_failure else (await EmbeddingService.target(db)).label,
        }
