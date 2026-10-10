"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useScroll, useTransform, type PanInfo, type Variants } from "motion/react";
import { ChevronLeft, ChevronRight, Plus, ShoppingBag, StickyNote, UtensilsCrossed } from "lucide-react";
import { apiFetch, getCustomerSessionContext, ApiException } from "@/lib/api";
import { announceDraftChanged, useDraftChanged } from "@/lib/draftEvents";
import { useLanguage } from "@/context/LanguageContext";
import { useGlobalDialog } from "@/components/common/GlobalDialogProvider";
import { useAssistantBubble } from "@/components/customer/AssistantBubbleProvider";
import { useGuestBasketCount } from "@/components/customer/GuestShell";
import { CategoryChipsSkeleton, MenuGridSkeleton } from "@/components/customer/GuestSkeletons";
import { CurrentOrder } from "@/components/customer/CurrentOrder";
import { ProductVisual } from "@/components/customer/ProductVisual";
import { AskAssistantButton } from "@/components/customer/AskAssistantButton";
import { Sheet, SheetBody, SheetFooter, SheetHeader } from "@/components/ui/Sheet";
import { QuantityStepper } from "@/components/ui/QuantityStepper";
import { ButtonSpinner, EmptyState, LoadError, Reveal, SlowNote } from "@/components/ui/Feedback";
import { StatusPill } from "@/components/ui/StatusPill";
import { Toast } from "@/components/ui/Toast";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { useScrollRow } from "@/lib/useScrollRow";
import { ScrollRowArrows } from "@/components/ui/ScrollRowArrows";
import { useAutoRetry } from "@/lib/useAutoRetry";
import { formatFils } from "@/lib/price";
import { otherLanguage } from "@/lib/otherLanguage";
import { EASE_OUT, spring } from "@/lib/motion";

interface Category {
  id: string;
  name_ar: string;
  name_en: string;
}

interface Product {
  id: string;
  category_id: string;
  name_ar: string;
  name_en: string;
  description_ar?: string;
  description_en?: string;
  price_minor: number;
  price_display_ar: string;
  price_display_en?: string;
  is_available: boolean;
  image_asset_url?: string;
  images: Array<{ id: string; url: string; sort_order: number }>;
}

interface DraftSummary {
  item_count: number;
}

/** Dish cards rise into place as they scroll into view, a few at a time. */
const cardReveal: Variants = {
  hidden: { opacity: 0, y: 18 },
  show: (index: number) => ({ opacity: 1, y: 0, transition: { duration: 0.5, ease: EASE_OUT, delay: (index % 6) * 0.045 } }),
};

/** The menu's opening: Jubran's rooftop at sunset, drifting gently as the page scrolls. */
function MenuHero({ onAsk }: { onAsk: () => void }) {
  const { t } = useLanguage();
  const { scrollY } = useScroll();
  const imageY = useTransform(scrollY, [0, 400], [0, 90]);
  const contentY = useTransform(scrollY, [0, 300], [0, -24]);
  const contentOpacity = useTransform(scrollY, [0, 260], [1, 0]);

  return (
    <section className="relative isolate -mt-16 overflow-hidden md:-mt-[4.5rem]" aria-labelledby="menu-title">
      <motion.div style={{ y: imageY }} className="absolute inset-0 -z-20" aria-hidden="true">
        <div className="absolute inset-0 bg-[url('/backdrops/rooftop.webp')] bg-cover bg-[center_40%] dark:brightness-[0.6]" />
      </motion.div>
      <div className="absolute inset-0 -z-10 bg-gradient-to-b from-canvas/30 via-canvas/70 to-canvas dark:from-canvas/40 dark:via-canvas/70" aria-hidden="true" />
      <div className="absolute inset-x-0 bottom-0 -z-10 h-24 bg-gradient-to-t from-canvas to-transparent" aria-hidden="true" />

      <motion.div
        style={{ y: contentY, opacity: contentOpacity }}
        className="mx-auto flex max-w-[1600px] flex-col items-center px-5 pb-12 pt-28 text-center sm:pb-16 md:pt-36 lg:pb-20"
      >
        {/* CSS entrances: the words show from the first paint, even before the scripts arrive. */}
        <p className="animate-rise inline-flex items-center gap-2 rounded-full border border-accent/30 bg-surface/70 px-3.5 py-1.5 text-xs font-semibold text-accent-ink shadow-hairline backdrop-blur [--rise-from:10px]">
          <span className="size-1.5 rounded-full bg-accent" aria-hidden="true" />
          {t("مطبخ شامي وعالمي • بوليفارد العبدلي", "Levantine & international • Abdali Boulevard")}
        </p>
        <h1
          id="menu-title"
          className="animate-rise mt-4 font-display text-[2.125rem] font-bold leading-[1.15] text-ink text-balance [--rise-delay:0.08s] [--rise-duration:0.75s] [--rise-from:18px] sm:text-5xl lg:text-6xl"
        >
          {t("قائمة الطعام", "Our menu")}
        </h1>
        <p className="animate-rise mt-3 max-w-md text-[0.9375rem] leading-relaxed text-ink-2 [--rise-delay:0.16s] sm:text-base">
          {t("تراث المشرق بروح جديدة فوق عمّان، يُحضَّر طازجاً لطاولتك.", "Levantine heritage, reimagined above Amman, made fresh for your table.")}
        </p>
        <AskAssistantButton label={t("محتار؟ اسأل نادلك الذكي", "Not sure? Ask your smart waiter")} onClick={onAsk} delay={0.24} className="mt-6" />
      </motion.div>
    </section>
  );
}

export default function MenuPage() {
  const { lang, dir, t } = useLanguage();
  const { showAlert } = useGlobalDialog();
  const openAssistant = useAssistantBubble();
  const [categories, setCategories] = useState<Category[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState<DraftSummary | null>(null);
  const [selectedProduct, setSelectedProduct] = useState<Product | null>(null);
  const [selectedImageIndex, setSelectedImageIndex] = useState(0);
  const [imageDirection, setImageDirection] = useState(1);
  const [note, setNote] = useState("");
  const [quantity, setQuantity] = useState(1);
  const [adding, setAdding] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [hasVisit, setHasVisit] = useState(false);
  // Failed loads in a row (the page retries by itself; see useAutoRetry).
  const [loadFailures, setLoadFailures] = useState(0);
  const loadingRef = useRef(false);
  const basketBeside = useMediaQuery("(min-width: 1280px)");
  const listTopRef = useRef<HTMLDivElement>(null);
  const chipsRef = useRef<HTMLDivElement>(null);
  const chipsRow = useScrollRow(chipsRef);

  const loadData = async () => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    try {
      setLoading(true);
      // The menu and whether this browser has a table visit, together: the page's layout
      // (with or without the basket beside it) is known when the dishes appear.
      const [catsData, prodsData, visit] = await Promise.all([
        apiFetch<Category[]>("/menu/categories"),
        apiFetch<Product[]>("/menu/products"),
        getCustomerSessionContext().catch(() => null),
      ]);
      setCategories(catsData);
      setProducts(prodsData);
      setHasVisit(Boolean(visit));
      setLoadFailures(0);

      // The basket count follows on its own (no active draft yet is fine).
      if (visit) apiFetch<DraftSummary>("/draft").then(setDraft).catch(() => undefined);
    } catch (err) {
      console.error("Error loading menu:", err);
      setLoadFailures((count) => count + 1);
    } finally {
      loadingRef.current = false;
      setLoading(false);
    }
  };

  useEffect(() => {
    void Promise.resolve().then(loadData);
  }, []);
  useAutoRetry(loadFailures, () => void loadData());
  useGuestBasketCount(draft?.item_count || 0);

  // First load only: later refreshes keep the dishes on screen.
  const failed = loadFailures > 0 && products.length === 0;
  const firstLoad = loading && products.length === 0 && !failed;

  // The assistant changed the basket: refresh the badge.
  useDraftChanged(() => {
    apiFetch<DraftSummary>("/draft").then(setDraft).catch(() => undefined);
  });

  const openProductDetails = (product: Product, event?: React.MouseEvent) => {
    event?.stopPropagation();
    setSelectedProduct(product);
    setSelectedImageIndex(0);
    setQuantity(1);
    setNote("");
  };

  const handleModalAdd = async () => {
    if (!selectedProduct || !selectedProduct.is_available) return;

    try {
      setAdding(true);
      const updatedDraft = await apiFetch<DraftSummary>("/draft/items", {
        method: "POST",
        body: JSON.stringify({
          product_id: selectedProduct.id,
          quantity,
          note: note.trim() || null,
        }),
      });
      setDraft(updatedDraft);
      // The basket beside the menu (large screens) shows it right away.
      announceDraftChanged();
      const prodName = lang === "ar" ? selectedProduct.name_ar : selectedProduct.name_en;
      setSelectedProduct(null);
      setNote("");
      setQuantity(1);
      setActionMessage(t(`تمت إضافة "${prodName}" إلى سلتك`, `Added "${prodName}" to your basket`));
      setTimeout(() => setActionMessage(null), 2500);
    } catch (err: unknown) {
      if (err instanceof ApiException) {
        void showAlert(err.message);
      }
    } finally {
      setAdding(false);
    }
  };

  const priceOf = (product: Product) =>
    lang === "en" && product.price_display_en ? product.price_display_en : product.price_display_ar;

  const filteredProducts = selectedCategory
    ? products.filter((p) => p.category_id === selectedCategory)
    : products;

  // Choosing a section brings the list back into view and the chosen chip into the row.
  const chooseCategory = (categoryId: string | null) => {
    setSelectedCategory(categoryId);
    const list = listTopRef.current;
    if (list && list.getBoundingClientRect().top < 0) {
      window.scrollTo({ top: window.scrollY + list.getBoundingClientRect().top - 140, behavior: "smooth" });
    }
    const chip = chipsRef.current?.querySelector<HTMLElement>(`[data-category="${categoryId ?? "all"}"]`);
    chip?.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
  };

  const categoryTabs = [
    { id: null as string | null, label: t("كل الأصناف", "All dishes"), count: products.length },
    ...categories.map((category) => ({
      id: category.id as string | null,
      label: lang === "ar" ? category.name_ar : category.name_en,
      count: products.filter((product) => product.category_id === category.id).length,
    })),
  ];

  const galleryImages = selectedProduct
    ? (selectedProduct.images?.length
      ? selectedProduct.images
      : selectedProduct.image_asset_url
        ? [{ id: "legacy", url: selectedProduct.image_asset_url, sort_order: 0 }]
        : [])
    : [];
  const activeIndex = galleryImages.length ? Math.min(selectedImageIndex, galleryImages.length - 1) : 0;
  const changeImage = (step: number) => {
    setImageDirection(step);
    setSelectedImageIndex((current) => (current + step + galleryImages.length) % galleryImages.length);
  };
  const onGalleryDragEnd = (_: unknown, info: PanInfo) => {
    if (galleryImages.length < 2) return;
    const swipe = info.offset.x;
    if (Math.abs(swipe) < 50) return;
    // Swiping towards the reading end shows the next photo.
    const towardsEnd = dir === "rtl" ? swipe > 0 : swipe < 0;
    changeImage(towardsEnd ? 1 : -1);
  };

  return (
    <div className="flex-1 overflow-x-clip bg-canvas pb-28 text-ink md:pb-16" dir={dir}>
      <MenuHero onAsk={openAssistant} />

      <div className={`mx-auto grid w-full max-w-[1600px] gap-8 px-4 sm:px-6 lg:px-8 ${hasVisit ? "xl:grid-cols-[minmax(0,1fr)_22.5rem] 2xl:grid-cols-[13.5rem_minmax(0,1fr)_22.5rem]" : "2xl:grid-cols-[13.5rem_minmax(0,1fr)]"}`}>
        {/* Sections rail (wide screens) */}
        <aside className="hidden 2xl:block" aria-label={t("تصنيفات القائمة", "Menu categories")}>
          {/* Never taller than the screen: a long list of sections scrolls inside the rail. */}
          <motion.nav layoutRoot className="sticky top-24 flex max-h-[calc(100dvh-7rem)] flex-col">
            <p className="mb-3 px-3 text-xs font-bold tracking-wide text-subtle">{t("الأقسام", "Sections")}</p>
            {firstLoad && <CategoryChipsSkeleton rail />}
            <motion.ul layoutScroll className={`min-h-0 flex-1 space-y-1 overflow-y-auto pb-2 ${firstLoad || failed ? "hidden" : ""}`}>
              {categoryTabs.map((tab) => {
                const active = selectedCategory === tab.id;
                return (
                  <li key={tab.id ?? "all"}>
                    <button
                      type="button"
                      onClick={() => chooseCategory(tab.id)}
                      aria-pressed={active}
                      className={`relative isolate flex w-full items-center justify-between gap-2 rounded-2xl px-3.5 py-3 text-start text-[0.9375rem] font-semibold transition-colors ${active ? "text-brand-soft-ink" : "text-ink-2 hover:bg-surface-3 hover:text-ink"}`}
                    >
                      {active && (
                        <motion.span layoutId="menu-rail" className="absolute inset-0 -z-10 rounded-2xl border border-brand-line bg-brand-soft" transition={spring.snappy}>
                          <span className="absolute inset-y-3 start-0 w-1 rounded-full bg-brand" />
                        </motion.span>
                      )}
                      <span className="truncate">{tab.label}</span>
                      <span className="text-xs font-bold tabular-nums text-subtle">{tab.count}</span>
                    </button>
                  </li>
                );
              })}
            </motion.ul>
          </motion.nav>
        </aside>

        <main className="@container min-w-0">
          {/* Sections chips (up to wide screens) */}
          <motion.div layoutRoot className={`sticky top-16 z-30 -mx-4 mb-5 bg-canvas/85 py-2.5 backdrop-blur-xl sm:-mx-6 md:top-[4.5rem] lg:-mx-8 2xl:hidden ${hasVisit ? "xl:me-0" : ""}`}>
            {firstLoad && <div className="px-4 sm:px-6 lg:px-8"><CategoryChipsSkeleton /></div>}
            {!firstLoad && !failed && <ScrollRowArrows edges={chipsRow.edges} onStep={chipsRow.step} className="mx-2 sm:mx-4 lg:mx-6" />}
            <motion.div layoutScroll ref={chipsRef} className={`no-scrollbar fade-x flex gap-2 overflow-x-auto px-4 sm:px-6 lg:px-8 ${firstLoad || failed ? "hidden" : ""}`} role="group" aria-label={t("تصنيفات القائمة", "Menu categories")}>
              {categoryTabs.map((tab) => {
                const active = selectedCategory === tab.id;
                return (
                  <button
                    key={tab.id ?? "all"}
                    data-category={tab.id ?? "all"}
                    type="button"
                    onClick={() => chooseCategory(tab.id)}
                    aria-pressed={active}
                    className={`relative isolate flex h-11 shrink-0 items-center gap-2 rounded-full border px-4 text-sm font-semibold transition-colors ${active ? "border-transparent text-on-brand" : "border-line bg-surface text-ink-2 hover:border-line-strong"}`}
                  >
                    {active && <motion.span layoutId="menu-chip" className="absolute inset-0 -z-10 rounded-full bg-brand shadow-glow" transition={spring.snappy} />}
                    <span>{tab.label}</span>
                    <span className={`text-xs font-bold tabular-nums ${active ? "text-on-brand/80" : "text-subtle"}`}>{tab.count}</span>
                  </button>
                );
              })}
            </motion.div>
          </motion.div>

          <div ref={listTopRef} />

          <Reveal
            ready={!firstLoad}
            label={t("جاري تحميل قائمة الطعام...", "Loading menu...")}
            skeleton={<><MenuGridSkeleton /><SlowNote className="mt-6" /></>}
          >
          {failed ? (
            <LoadError title={t("ما قدرنا نحمّل القائمة", "We couldn't load the menu")} onRetry={() => void loadData()} retrying={loading} />
          ) : filteredProducts.length === 0 ? (
            <EmptyState
              icon={<UtensilsCrossed className="size-7" strokeWidth={1.75} aria-hidden="true" />}
              title={t("لا توجد أصناف هنا حالياً", "Nothing here right now")}
              description={t("لا توجد أصناف في هذا القسم حالياً.", "There are no items in this category right now.")}
            />
          ) : (
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={selectedCategory ?? "all"}
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8, transition: { duration: 0.15 } }}
                transition={{ duration: 0.4, ease: EASE_OUT }}
                className="space-y-12"
              >
                {(selectedCategory ? categories.filter((category) => category.id === selectedCategory) : categories).map((category) => {
                  const categoryProducts = filteredProducts.filter((product) => product.category_id === category.id);
                  if (categoryProducts.length === 0) return null;

                  return (
                    <section key={category.id} className="scroll-mt-40" aria-labelledby={`category-${category.id}`}>
                      <header className="mb-5 flex items-end justify-between gap-3 border-b border-line pb-3">
                        <div className="min-w-0">
                          <h2 id={`category-${category.id}`} className="font-display text-[1.375rem] font-bold leading-tight text-ink sm:text-2xl">
                            {lang === "ar" ? category.name_ar : category.name_en}
                          </h2>
                          <p className="mt-1.5 text-end text-xs text-subtle" {...otherLanguage(lang)}>{lang === "ar" ? category.name_en : category.name_ar}</p>
                        </div>
                        <span className="shrink-0 rounded-full bg-surface-3 px-3 py-1 text-xs font-bold text-ink-2">
                          {categoryProducts.length} {t("أصناف", "dishes")}
                        </span>
                      </header>
                      <div className="grid grid-cols-1 gap-3 @[30rem]:grid-cols-2 @[30rem]:gap-4 @[49rem]:grid-cols-3 @[49rem]:gap-5 @[68rem]:grid-cols-4">
                        {categoryProducts.map((prod, index) => {
                          const title = lang === "ar" ? prod.name_ar : prod.name_en;
                          const subTitle = lang === "ar" ? prod.name_en : prod.name_ar;
                          const desc = lang === "ar" ? prod.description_ar : prod.description_en || prod.description_ar;
                          const photo = prod.images?.[0]?.url || prod.image_asset_url;

                          return (
                            <motion.article
                              key={prod.id}
                              variants={cardReveal}
                              custom={index}
                              initial="hidden"
                              whileInView="show"
                              viewport={{ once: true, amount: 0.15 }}
                              whileHover={{ y: -4 }}
                              whileTap={{ scale: 0.985 }}
                              onClick={(event) => openProductDetails(prod, event)}
                              className={`group relative flex cursor-pointer gap-3.5 rounded-3xl border border-line bg-surface p-3 shadow-card transition-[box-shadow,border-color] duration-300 hover:border-line-strong hover:shadow-lift @[30rem]:flex-col @[30rem]:gap-0 @[30rem]:p-0 ${!prod.is_available ? "opacity-75" : ""}`}
                            >
                              <div className="relative shrink-0">
                                <ProductVisual
                                  url={photo}
                                  alt={title}
                                  className={`size-[6.5rem] rounded-2xl @[30rem]:aspect-[4/3] @[30rem]:size-auto @[30rem]:w-full @[30rem]:rounded-b-none @[30rem]:rounded-t-3xl ${!prod.is_available ? "grayscale" : ""}`}
                                  imgClassName="transition-transform duration-700 ease-out group-hover:scale-[1.06]"
                                />
                                {prod.images && prod.images.length > 1 && (
                                  <span className="absolute bottom-2 start-2 rounded-full bg-black/55 px-2 py-0.5 text-[0.6875rem] font-semibold text-white backdrop-blur-sm">+{prod.images.length - 1}</span>
                                )}
                                {!prod.is_available && (
                                  <span className="absolute inset-x-2 top-2 flex justify-center @[30rem]:inset-x-auto @[30rem]:start-3 @[30rem]:top-3">
                                    <StatusPill tone="neutral" size="sm" className="bg-surface/90 backdrop-blur">{t("غير متوفر", "Unavailable")}</StatusPill>
                                  </span>
                                )}
                              </div>

                              <div className="flex min-w-0 flex-1 flex-col @[30rem]:p-4">
                                <h3 className="line-clamp-2 text-[0.9375rem] font-bold leading-snug text-ink sm:text-base">{title}</h3>
                                <p className="line-clamp-1 text-end text-xs text-subtle" {...otherLanguage(lang)}>{subTitle}</p>
                                {desc && <p className="mt-1.5 line-clamp-2 text-[0.8125rem] leading-relaxed text-muted">{desc}</p>}
                                <div className="mt-auto flex items-center justify-between gap-2 pt-3">
                                  <span className="font-display text-lg font-bold leading-none tabular-nums text-brand">{priceOf(prod)}</span>
                                  <button
                                    type="button"
                                    onClick={(event) => openProductDetails(prod, event)}
                                    disabled={!prod.is_available || adding}
                                    aria-label={prod.is_available ? `${t("أضف إلى طلبك", "Add to order")}: ${title}` : t("غير متوفر", "Unavailable")}
                                    className="inline-flex h-10 items-center gap-1.5 rounded-full bg-brand ps-3 pe-4 text-sm font-semibold text-on-brand shadow-glow transition-[background-color,transform] hover:bg-brand-hover active:scale-95 disabled:bg-surface-3 disabled:text-subtle disabled:shadow-none"
                                  >
                                    <Plus className="size-4" aria-hidden="true" />
                                    <span className="hidden min-[360px]:inline">{prod.is_available ? t("أضف", "Add") : t("نفد", "Out")}</span>
                                  </button>
                                </div>
                              </div>
                            </motion.article>
                          );
                        })}
                      </div>
                    </section>
                  );
                })}
              </motion.div>
            </AnimatePresence>
          )}
          </Reveal>
        </main>

        {/* The basket beside the menu (large screens) */}
        {basketBeside && hasVisit && (
          <aside aria-label={t("سلتك", "Your basket")}>
            <div className="sticky top-24 max-h-[calc(100dvh-12.5rem)] overflow-y-auto overscroll-contain rounded-[1.75rem] border border-line bg-surface/80 p-5 shadow-card backdrop-blur">
              <CurrentOrder
                variant="panel"
                onSubmitted={() => {
                  setActionMessage(t("تم إرسال طلبك للمطبخ ✓ تابع حالته من «طلباتي».", "Your order was sent to the kitchen ✓ Follow it in “My orders”."));
                  setTimeout(() => setActionMessage(null), 3500);
                }}
                onDraftCountChange={(count) => setDraft({ item_count: count })}
              />
            </div>
          </aside>
        )}
      </div>

      <Toast message={actionMessage} />

      {/* Dish details: bottom sheet on phones, a two-column dialog on larger screens */}
      <Sheet open={selectedProduct !== null} onClose={() => setSelectedProduct(null)} labelledBy="product-dialog-title" size="xl">
        {selectedProduct && (
          <>
            <SheetBody flush>
              <div className="grid sm:grid-cols-2">
                {/* Photos */}
                <div className="px-4 pt-1 sm:p-6 sm:pe-3">
                  <div className="relative aspect-[4/3] overflow-hidden rounded-3xl bg-surface-3 shadow-card sm:aspect-square">
                    {galleryImages.length > 0 ? (
                      <AnimatePresence initial={false} custom={imageDirection} mode="popLayout">
                        <motion.div
                          key={galleryImages[activeIndex].id}
                          custom={imageDirection}
                          initial={{ opacity: 0, x: (dir === "rtl" ? -1 : 1) * imageDirection * 60 }}
                          animate={{ opacity: 1, x: 0 }}
                          exit={{ opacity: 0, x: (dir === "rtl" ? 1 : -1) * imageDirection * 60 }}
                          transition={{ duration: 0.35, ease: EASE_OUT }}
                          drag={galleryImages.length > 1 ? "x" : false}
                          dragConstraints={{ left: 0, right: 0 }}
                          dragElastic={0.35}
                          onDragEnd={onGalleryDragEnd}
                          className="absolute inset-0 touch-pan-y"
                        >
                          <ProductVisual url={galleryImages[activeIndex].url} alt={lang === "ar" ? selectedProduct.name_ar : selectedProduct.name_en} className="h-full w-full" imgClassName="pointer-events-none" />
                        </motion.div>
                      </AnimatePresence>
                    ) : (
                      <ProductVisual url={null} alt="" className="h-full w-full" iconClassName="size-14" />
                    )}
                    {galleryImages.length > 1 && (
                      <>
                        <span className="absolute bottom-3 start-3 rounded-full bg-black/55 px-2.5 py-1 text-xs font-bold tabular-nums text-white backdrop-blur-sm">{activeIndex + 1} / {galleryImages.length}</span>
                        <button type="button" onClick={() => changeImage(-1)} aria-label={t("الصورة السابقة", "Previous photo")} className="absolute start-3 top-1/2 hidden size-10 -translate-y-1/2 items-center justify-center rounded-full bg-surface/90 text-ink shadow-lift backdrop-blur transition-transform hover:scale-105 sm:flex">
                          <ChevronLeft className="size-5 rtl:-scale-x-100" aria-hidden="true" />
                        </button>
                        <button type="button" onClick={() => changeImage(1)} aria-label={t("الصورة التالية", "Next photo")} className="absolute end-3 top-1/2 hidden size-10 -translate-y-1/2 items-center justify-center rounded-full bg-surface/90 text-ink shadow-lift backdrop-blur transition-transform hover:scale-105 sm:flex">
                          <ChevronRight className="size-5 rtl:-scale-x-100" aria-hidden="true" />
                        </button>
                        <div className="absolute inset-x-0 bottom-3 flex justify-center gap-1.5 sm:hidden" aria-hidden="true">
                          {galleryImages.map((image, index) => (
                            <span key={image.id} className={`h-1.5 rounded-full bg-white shadow transition-all ${index === activeIndex ? "w-5" : "w-1.5 opacity-60"}`} />
                          ))}
                        </div>
                      </>
                    )}
                  </div>
                  {galleryImages.length > 1 && (
                    <div className="no-scrollbar mt-3 hidden gap-2 overflow-x-auto sm:flex">
                      {galleryImages.map((image, index) => (
                        <button
                          key={image.id}
                          type="button"
                          onClick={() => { setImageDirection(index > activeIndex ? 1 : -1); setSelectedImageIndex(index); }}
                          aria-label={`${t("عرض صورة", "Show photo")} ${index + 1}`}
                          aria-pressed={activeIndex === index}
                          className={`size-16 shrink-0 overflow-hidden rounded-2xl border-2 transition-all ${activeIndex === index ? "border-brand shadow-glow" : "border-transparent opacity-60 hover:opacity-100"}`}
                        >
                          <ProductVisual url={image.url} alt="" className="h-full w-full" />
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                {/* Details */}
                <div className="flex flex-col pb-5 pt-2 sm:pb-6 sm:pt-0">
                  <SheetHeader
                    id="product-dialog-title"
                    title={lang === "ar" ? selectedProduct.name_ar : selectedProduct.name_en}
                    subtitle={<span {...otherLanguage(lang)}>{lang === "ar" ? selectedProduct.name_en : selectedProduct.name_ar}</span>}
                    onClose={() => setSelectedProduct(null)}
                  />
                  <div className="-mt-1 flex items-center gap-2 px-5 sm:px-6">
                    <span className="font-display text-2xl font-bold leading-none tabular-nums text-brand">{priceOf(selectedProduct)}</span>
                    {selectedProduct.is_available
                      ? <StatusPill tone="success" dot size="sm">{t("متوفر الآن", "Available now")}</StatusPill>
                      : <StatusPill tone="neutral" size="sm">{t("غير متوفر حالياً", "Unavailable right now")}</StatusPill>}
                  </div>

                  <div className="space-y-5 px-5 pt-5 sm:px-6">
                    {(selectedProduct.description_ar || selectedProduct.description_en) && (
                      <p className="text-[0.9375rem] leading-relaxed text-ink-2">
                        {lang === "ar"
                          ? selectedProduct.description_ar
                          : selectedProduct.description_en || selectedProduct.description_ar}
                      </p>
                    )}

                    <div className="flex items-center justify-between gap-4 rounded-2xl border border-line bg-surface-2 p-3 ps-4">
                      <span id="product-quantity-label" className="font-semibold text-ink">{t("الكمية", "Quantity")}</span>
                      <QuantityStepper
                        value={quantity}
                        onDecrement={() => setQuantity((q) => Math.max(1, q - 1))}
                        onIncrement={() => setQuantity((q) => Math.min(50, q + 1))}
                        decrementDisabled={quantity <= 1}
                        incrementDisabled={quantity >= 50}
                        label={t("الكمية", "Quantity")}
                      />
                    </div>

                    {/* Custom Notes Input (REQ-022: plain text, max 200 chars) */}
                    <div>
                      <div className="mb-1.5 flex items-center justify-between">
                        <label htmlFor="product-note" className="field-label mb-0 flex items-center gap-1.5">
                          <StickyNote className="size-4 text-accent" aria-hidden="true" />
                          {t("ملاحظات خاصة (اختياري)", "Special instructions (optional)")}
                        </label>
                        <span id="product-note-count" className="text-xs tabular-nums text-subtle">{note.length}/200</span>
                      </div>
                      <textarea
                        id="product-note"
                        aria-describedby="product-note-count"
                        value={note}
                        onChange={(e) => setNote(e.target.value.slice(0, 200))}
                        maxLength={200}
                        placeholder={t(
                          "مثال: زيت زيتون زيادة، بدون شطة، الخبز مقمر...",
                          "e.g. extra olive oil, no chili, well-toasted bread..."
                        )}
                        rows={2}
                        className="input min-h-[4.5rem]"
                      />
                    </div>
                  </div>
                </div>
              </div>
            </SheetBody>
            <SheetFooter>
              <button type="button" onClick={() => setSelectedProduct(null)} className="btn btn-ghost hidden sm:inline-flex">
                {t("إلغاء", "Cancel")}
              </button>
              <motion.button
                type="button"
                onClick={handleModalAdd}
                disabled={!selectedProduct.is_available || adding}
                whileTap={{ scale: 0.98 }}
                className="btn btn-primary btn-lg flex-1 justify-between"
              >
                <span className="flex items-center gap-2">
                  {adding ? <ButtonSpinner className="size-5" /> : <ShoppingBag className="size-5" aria-hidden="true" />}
                  {selectedProduct.is_available
                    ? (adding ? t("جاري الإضافة...", "Adding...") : t("أضف إلى طلبك", "Add to order"))
                    : t("الصنف غير متوفر", "Item unavailable")}
                </span>
                {selectedProduct.is_available && (
                  <span className="rounded-xl bg-white/15 px-2.5 py-1 text-sm font-bold tabular-nums">
                    {formatFils(selectedProduct.price_minor * quantity, lang)}
                  </span>
                )}
              </motion.button>
            </SheetFooter>
          </>
        )}
      </Sheet>
    </div>
  );
}
