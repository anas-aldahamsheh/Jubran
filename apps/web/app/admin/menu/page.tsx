"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import {
  BookOpenText, CircleAlert, Clock3, Info, LoaderCircle, MapPin, Pencil, Phone, Plus, Save, Search, Store, Trash2,
  TriangleAlert, UtensilsCrossed, X,
} from "lucide-react";
import { AdminDishesSkeleton, RestaurantFormSkeleton } from "@/components/admin/AdminSkeletons";
import { CategoriesEditor } from "@/components/admin/CategoriesEditor";
import { SelectField } from "@/components/common/SelectField";
import { ProductImagePicker, type ManagedProductImage } from "@/components/admin/ProductImagePicker";
import { ProductVisual } from "@/components/customer/ProductVisual";
import { Sheet, SheetBody, SheetFooter, SheetHeader } from "@/components/ui/Sheet";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Switch } from "@/components/ui/Switch";
import { EmptyState, LoadError, Reveal, SlowNote } from "@/components/ui/Feedback";
import { Toast } from "@/components/ui/Toast";
import { apiFetch, ApiException } from "@/lib/api";
import { useAutoRetry } from "@/lib/useAutoRetry";
import { priceToFils } from "@/lib/price";
import { useLanguage } from "@/context/LanguageContext";
import { EASE_OUT, spring } from "@/lib/motion";

interface OpeningHour {
  day_of_week: number;
  opens_at: string;
  closes_at: string;
  /** A note for the day, e.g. "closed during Friday prayer" (the assistant says it too). */
  notes_ar?: string | null;
  notes_en?: string | null;
}

interface RestaurantProfile {
  id: string;
  name_ar: string;
  name_en: string;
  about_ar: string;
  about_en: string;
  phone: string;
  branch?: {
    id: string;
    name_ar: string;
    name_en: string;
    address_ar: string;
    address_en: string;
    phone: string;
    opening_hours: OpeningHour[];
  };
}

interface Product {
  id: string;
  category_id: string;
  name_ar: string;
  name_en: string;
  description_ar?: string;
  description_en?: string;
  price_minor: number;
  price_jod: string;
  price_display_ar: string;
  price_display_en: string;
  is_available: boolean;
  image_asset_url?: string;
  images: Array<{ id: string; url: string; legacy?: boolean; sort_order: number }>;
}

interface Category {
  id: string;
  name_ar: string;
  name_en: string;
}

const DAYS_MAP: Array<{ ar: string; en: string }> = [
  { ar: "الأحد", en: "Sunday" },
  { ar: "الاثنين", en: "Monday" },
  { ar: "الثلاثاء", en: "Tuesday" },
  { ar: "الأربعاء", en: "Wednesday" },
  { ar: "الخميس", en: "Thursday" },
  { ar: "الجمعة", en: "Friday" },
  { ar: "السبت", en: "Saturday" }
];

function normalizeProductSearch(value: string): string[] {
  const normalized = value
    .normalize("NFKD")
    .replace(/[\u064B-\u065F\u0670\u0640]/g, "")
    .replace(/[أإآ]/g, "ا")
    .replace(/ى/g, "ي")
    .replace(/ة/g, "ه")
    .toLocaleLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
  return normalized ? normalized.split(/\s+/) : [];
}

const PRICE_HINT = {
  ar: "يرجى إدخال سعر صحيح بالدينار الأردني أكبر من صفر، بحد أقصى 3 منازل عشرية (مثل 3.450).",
  en: "Please enter a valid price in Jordanian dinars, above zero, with at most 3 decimal places (e.g. 3.450).",
};

export default function AdminMenuPage() {
  const router = useRouter();
  const { lang, dir, t } = useLanguage();
  /** API text that exists in both languages: the current language, falling back to Arabic. */
  const localized = (ar: string | undefined, en: string | undefined) => (lang === "en" && en ? en : ar) ?? "";

  // Restaurant State
  const [loadingProfile, setLoadingProfile] = useState(true);
  const [savingProfile, setSavingProfile] = useState(false);

  // Form Fields for Restaurant Profile
  const [nameAr, setNameAr] = useState("");
  const [nameEn, setNameEn] = useState("");
  const [aboutAr, setAboutAr] = useState("");
  const [aboutEn, setAboutEn] = useState("");
  const [phone, setPhone] = useState("");
  const [branchAddressAr, setBranchAddressAr] = useState("");
  const [branchAddressEn, setBranchAddressEn] = useState("");
  const [branchPhone, setBranchPhone] = useState("");
  const [branchNameAr, setBranchNameAr] = useState("");
  const [branchNameEn, setBranchNameEn] = useState("");
  const [openingHours, setOpeningHours] = useState<OpeningHour[]>([]);

  // Menu Products State
  const [products, setProducts] = useState<Product[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [loadingProducts, setLoadingProducts] = useState(true);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  // Failed loads in a row (retried by itself; see useAutoRetry).
  const [loadFailures, setLoadFailures] = useState(0);
  const loadingRef = useRef(false);

  // Feedback Toast Banner
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const showFeedback = (text: string, type: "success" | "error" = "success") => {
    setFeedback({ type, text });
    setTimeout(() => {
      setFeedback(null);
    }, 4000);
  };

  const saveProductImages = async (productId: string, images: ManagedProductImage[]) => {
    const form = new FormData();
    form.set("retained_image_ids", JSON.stringify(images.filter((image) => image.id).map((image) => image.id)));
    images.forEach((image) => {
      if (image.file) form.append("files", image.file);
    });
    return apiFetch<Product>(`/admin/menu/products/${productId}/images`, { method: "PUT", body: form });
  };

  /** Only send photos when the admin added, removed or reordered some. */
  const imagesChanged = (original: Product, images: ManagedProductImage[]) => {
    const before = (original.images || []).map((image) => image.id);
    const after = images.filter((image) => image.id).map((image) => image.id);
    return images.some((image) => image.file)
      || before.length !== after.length
      || before.some((id, index) => id !== after[index]);
  };

  // Add Product Modal State
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [addCategoryId, setAddCategoryId] = useState("");
  const [addNameAr, setAddNameAr] = useState("");
  const [addNameEn, setAddNameEn] = useState("");
  const [addDescAr, setAddDescAr] = useState("");
  const [addDescEn, setAddDescEn] = useState("");
  const [addPriceJod, setAddPriceJod] = useState("");
  const [addIsAvailable, setAddIsAvailable] = useState(true);
  const [addImageUrl, setAddImageUrl] = useState("");
  const [addImages, setAddImages] = useState<ManagedProductImage[]>([]);
  const [savingNewProduct, setSavingNewProduct] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  // Edit Product Modal State
  const [editingProduct, setEditingProduct] = useState<Product | null>(null);
  const [editCategoryId, setEditCategoryId] = useState("");
  const [editNameAr, setEditNameAr] = useState("");
  const [editNameEn, setEditNameEn] = useState("");
  const [editDescAr, setEditDescAr] = useState("");
  const [editDescEn, setEditDescEn] = useState("");
  const [editPriceJod, setEditPriceJod] = useState("");
  const [editIsAvailable, setEditIsAvailable] = useState(true);
  const [editImageUrl, setEditImageUrl] = useState("");
  const [editImages, setEditImages] = useState<ManagedProductImage[]>([]);
  const [savingProduct, setSavingProduct] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  // Delete Product Confirmation Modal State
  const [deletingProduct, setDeletingProduct] = useState<Product | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  // Active Tab
  const [activeTab, setActiveTab] = useState<"products" | "restaurant">("products");

  const loadData = async () => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    try {
      setLoadingProfile(true);
      setLoadingProducts(true);

      const [restData, prodsData, catsData] = await Promise.all([
        apiFetch<RestaurantProfile>("/admin/restaurant"),
        apiFetch<Product[]>("/admin/menu/products"),
        apiFetch<{ categories: Category[] }>("/admin/menu/categories"),
      ]);

      setNameAr(restData.name_ar);
      setNameEn(restData.name_en);
      setAboutAr(restData.about_ar);
      setAboutEn(restData.about_en);
      setPhone(restData.phone);
      if (restData.branch) {
        setBranchNameAr(restData.branch.name_ar);
        setBranchNameEn(restData.branch.name_en);
        setBranchAddressAr(restData.branch.address_ar);
        setBranchAddressEn(restData.branch.address_en);
        setBranchPhone(restData.branch.phone);
        setOpeningHours(restData.branch.opening_hours);
      }

      setProducts(prodsData);
      setCategories(catsData.categories);
      if (catsData.categories.length > 0 && !addCategoryId) {
        setAddCategoryId(catsData.categories[0].id);
      }
      setLoadFailures(0);
    } catch (err: unknown) {
      if (err instanceof ApiException && (err.code === "UNAUTHENTICATED" || err.code === "FORBIDDEN")) {
        router.push("/login");
      } else {
        setLoadFailures((count) => count + 1);
      }
    } finally {
      loadingRef.current = false;
      setLoadingProfile(false);
      setLoadingProducts(false);
    }
  };

  useEffect(() => {
    void Promise.resolve().then(loadData);
    // Loaded once when the page opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Nothing loaded yet and the load failed: it keeps trying by itself.
  const loadFailed = loadFailures > 0 && products.length === 0 && !nameAr;
  useAutoRetry(loadFailed ? loadFailures : 0, () => void loadData());

  // Save Restaurant Profile
  const handleSaveRestaurant = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      setSavingProfile(true);
      const updated = await apiFetch<RestaurantProfile>("/admin/restaurant", {
        method: "PATCH",
        body: JSON.stringify({
          name_ar: nameAr,
          name_en: nameEn,
          about_ar: aboutAr,
          about_en: aboutEn,
          phone: phone,
          branch_address_ar: branchAddressAr,
          branch_address_en: branchAddressEn,
          branch_phone: branchPhone,
          branch_name_ar: branchNameAr,
          branch_name_en: branchNameEn,
          opening_hours: openingHours,
        }),
      });
      if (updated.branch) setOpeningHours(updated.branch.opening_hours);
      showFeedback(t("تم حفظ معلومات المطعم وتحديثها فورياً في النظام!", "Restaurant details saved and updated right away!"));
    } catch (err: unknown) {
      if (err instanceof ApiException) {
        showFeedback(err.message, "error");
      }
    } finally {
      setSavingProfile(false);
    }
  };

  // Instant Availability Toggle (REQ-024)
  const handleToggleAvailability = async (product: Product, e: React.MouseEvent) => {
    e.stopPropagation();
    const newStatus = !product.is_available;

    // Optimistic UI update
    setProducts((prev) =>
      prev.map((p) => (p.id === product.id ? { ...p, is_available: newStatus } : p))
    );
    setTogglingId(product.id);

    try {
      const updated = await apiFetch<Product>(`/admin/menu/products/${product.id}/availability`, {
        method: "PATCH",
        body: JSON.stringify({ is_available: newStatus }),
      });
      setProducts((prev) =>
        prev.map((p) => (p.id === product.id ? { ...p, is_available: updated.is_available } : p))
      );
      showFeedback(
        newStatus
          ? t(`تم تفعيل توفر طبق "${product.name_ar}" للطلب والمساعد الذكي.`, `"${localized(product.name_ar, product.name_en)}" is now available for ordering and in the AI assistant.`)
          : t(`تم تحويل طبق "${product.name_ar}" إلى غير متوفر حالياً.`, `"${localized(product.name_ar, product.name_en)}" is now marked as unavailable.`)
      );
    } catch (err: unknown) {
      // Revert on failure
      setProducts((prev) =>
        prev.map((p) => (p.id === product.id ? { ...p, is_available: !newStatus } : p))
      );
      if (err instanceof ApiException) {
        showFeedback(err.message, "error");
      }
    } finally {
      setTogglingId(null);
    }
  };

  // Open Add Product Modal
  const openAddModal = () => {
    const category = categories.find((item) => item.id === selectedCategory) || categories[0];
    setAddCategoryId(category?.id || "");
    setAddNameAr("");
    setAddNameEn("");
    setAddDescAr("");
    setAddDescEn("");
    setAddPriceJod("");
    setAddIsAvailable(true);
    setAddImageUrl("");
    setAddImages([]);
    setAddError(null);
    setIsAddModalOpen(true);
  };

  // Submit Add Product Form
  const handleCreateProduct = async (e: React.FormEvent) => {
    e.preventDefault();
    const priceMinor = priceToFils(addPriceJod);
    if (priceMinor === null) {
      setAddError(t(PRICE_HINT.ar, PRICE_HINT.en));
      return;
    }

    if (!categories.some((category) => category.id === addCategoryId)) {
      setAddError(t("يرجى اختيار تصنيف الطبق.", "Please choose a category for the dish."));
      return;
    }

    try {
      setSavingNewProduct(true);
      setAddError(null);

      const created = await apiFetch<Product>("/admin/menu/products", {
        method: "POST",
        body: JSON.stringify({
          category_id: addCategoryId,
          name_ar: addNameAr.trim(),
          name_en: addNameEn.trim(),
          description_ar: addDescAr.trim() || undefined,
          description_en: addDescEn.trim() || undefined,
          price_minor: priceMinor,
          is_available: addIsAvailable,
          image_asset_url: addImageUrl.trim() || undefined,
        }),
      });

      if (addImages.length) {
        try {
          const saved = await saveProductImages(created.id, addImages);
          setProducts((prev) => [...prev, saved]);
          setIsAddModalOpen(false);
          showFeedback(t(`تمت إضافة طبق "${saved.name_ar}" بنجاح وأصبح متاحاً للطلب والمساعد الذكي!`, `"${localized(saved.name_ar, saved.name_en)}" was added and is now available for ordering and in the AI assistant!`));
        } catch (imageError) {
          setProducts((prev) => [...prev, created]);
          setIsAddModalOpen(false);
          openEditModal(created);
          setEditImages(addImages);
          setEditError(imageError instanceof ApiException ? imageError.message : t("تم إنشاء الطبق لكن تعذر حفظ الصور. أعد المحاولة.", "The dish was created, but its photos could not be saved. Please try again."));
        }
        return;
      }

      setProducts((prev) => [...prev, created]);
      setIsAddModalOpen(false);
      showFeedback(t(`تمت إضافة طبق "${created.name_ar}" بنجاح وأصبح متاحاً للطلب والمساعد الذكي!`, `"${localized(created.name_ar, created.name_en)}" was added and is now available for ordering and in the AI assistant!`));
    } catch (err: unknown) {
      if (err instanceof ApiException) {
        setAddError(err.message);
      } else {
        setAddError(t("حدث خطأ أثناء إضافة الصنف الجديد.", "Something went wrong while adding the new dish."));
      }
    } finally {
      setSavingNewProduct(false);
    }
  };

  // Open Edit Product Modal
  const openEditModal = (product: Product) => {
    setEditingProduct(product);
    setEditCategoryId(product.category_id);
    setEditNameAr(product.name_ar);
    setEditNameEn(product.name_en);
    setEditDescAr(product.description_ar || "");
    setEditDescEn(product.description_en || "");
    setEditPriceJod(product.price_jod);
    setEditIsAvailable(product.is_available);
    setEditImageUrl(product.image_asset_url || "");
    setEditImages((product.images || []).map((image) => ({ key: image.id, id: image.id, url: image.url })));
    setEditError(null);
  };

  // Submit Edit Product Form
  const handleSaveProduct = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingProduct) return;

    const priceMinor = priceToFils(editPriceJod);
    if (priceMinor === null) {
      setEditError(t(PRICE_HINT.ar, PRICE_HINT.en));
      return;
    }

    try {
      setSavingProduct(true);
      setEditError(null);
      // Empty fields are sent as "" so a description or image link can be removed.
      let updated = await apiFetch<Product>(`/admin/menu/products/${editingProduct.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          category_id: editCategoryId || undefined,
          name_ar: editNameAr.trim(),
          name_en: editNameEn.trim(),
          description_ar: editDescAr.trim(),
          description_en: editDescEn.trim(),
          price_minor: priceMinor,
          is_available: editIsAvailable,
          image_asset_url: editImageUrl.trim(),
        }),
      });
      setProducts((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));

      if (imagesChanged(editingProduct, editImages)) {
        try {
          updated = await saveProductImages(editingProduct.id, editImages);
          setProducts((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
        } catch (imageError) {
          // The details are saved; keep the dialog open so only the photos are retried.
          setEditingProduct(updated);
          const reason = imageError instanceof ApiException ? imageError.message : t("تعذر الاتصال بالخادم.", "Could not reach the server.");
          setEditError(t(`تم حفظ بيانات الصنف، لكن لم تُحفظ الصور: ${reason} اضغط حفظ مرة أخرى لإعادة محاولة الصور فقط.`, `The dish details were saved, but the photos were not: ${reason} Press Save again to retry the photos only.`));
          return;
        }
      }

      setEditingProduct(null);
      showFeedback(t(`تم تحديث بيانات طبق "${updated.name_ar}" بنجاح.`, `"${localized(updated.name_ar, updated.name_en)}" was updated.`));
    } catch (err: unknown) {
      if (err instanceof ApiException) {
        setEditError(err.message);
      } else {
        setEditError(t("حدث خطأ أثناء تحديث بيانات الصنف.", "Something went wrong while updating the dish."));
      }
    } finally {
      setSavingProduct(false);
    }
  };

  // Open Delete Confirmation Modal
  const openDeleteModal = (product: Product, e: React.MouseEvent) => {
    e.stopPropagation();
    setDeletingProduct(product);
  };

  // Submit Delete Product
  const handleDeleteProduct = async () => {
    if (!deletingProduct) return;

    try {
      setIsDeleting(true);
      await apiFetch<{ success: boolean; deleted: { id: string; name_ar: string } }>(
        `/admin/menu/products/${deletingProduct.id}`,
        { method: "DELETE" }
      );

      // Remove from state immediately
      setProducts((prev) => prev.filter((p) => p.id !== deletingProduct.id));
      showFeedback(t(`تم حذف طبق "${deletingProduct.name_ar}" نهائياً من القائمة والمساعد الذكي.`, `"${localized(deletingProduct.name_ar, deletingProduct.name_en)}" was permanently removed from the menu and the AI assistant.`));
      setDeletingProduct(null);
    } catch (err: unknown) {
      if (err instanceof ApiException) {
        showFeedback(err.message, "error");
      } else {
        showFeedback(t("حدث خطأ أثناء حذف الصنف.", "Something went wrong while deleting the dish."), "error");
      }
    } finally {
      setIsDeleting(false);
    }
  };

  // Filtered Products
  const searchTerms = normalizeProductSearch(searchQuery);
  const filteredProducts = products.filter((p) => {
    const matchesCat = !selectedCategory || p.category_id === selectedCategory;
    const matchesName = (name: string) => {
      const normalizedName = normalizeProductSearch(name).join(" ");
      return searchTerms.every((term) => normalizedName.includes(term));
    };
    // Match all entered words against one product name (AR or EN); descriptions
    // are intentionally excluded so ingredients do not produce unrelated hits.
    const matchesQuery = searchTerms.length === 0 || matchesName(p.name_ar) || matchesName(p.name_en);
    return matchesCat && matchesQuery;
  });

  // Category choices for the add/edit forms: current language first, the other one in brackets.
  const categoryOptions = categories.map((category) => ({
    value: category.id,
    label: lang === "en" && category.name_en ? `${category.name_en} (${category.name_ar})` : `${category.name_ar} (${category.name_en})`,
  }));

  const refreshCategories = async () => {
    const current = await apiFetch<{ categories: Category[] }>("/admin/menu/categories");
    setCategories(current.categories);
    setSelectedCategory(null);
    if (!current.categories.some((category) => category.id === addCategoryId)) {
      setAddCategoryId(current.categories[0]?.id || "");
    }
  };

  const availableCount = products.filter((p) => p.is_available).length;
  const categoryTabs = [
    { id: null as string | null, label: t("الكل", "All"), count: products.length },
    ...categories.map((cat) => ({ id: cat.id as string | null, label: localized(cat.name_ar, cat.name_en), count: products.filter((p) => p.category_id === cat.id).length })),
  ];

  /** The dish form shared by "add" and "edit" (same fields, different state). */
  const dishFields = (mode: "add" | "edit") => {
    const add = mode === "add";
    const prefix = add ? "new-dish" : "edit-dish";
    const price = add ? addPriceJod : editPriceJod;
    return (
      <div className="space-y-4">
        <div>
          <label htmlFor={`${prefix}-category`} className="field-label">{t("تصنيف الطبق", "Dish category")}</label>
          <SelectField id={`${prefix}-category`}
            value={add ? addCategoryId : editCategoryId}
            onValueChange={add ? setAddCategoryId : setEditCategoryId}
            ariaLabel={add ? t("تصنيف الطبق الجديد", "New dish category") : t("تصنيف الطبق", "Dish category")}
            required
            options={categoryOptions}
          />
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor={`${prefix}-name-ar`} className="field-label">{add ? t("اسم الطبق بالعربية *", "Arabic dish name *") : t("اسم الصنف (عربي) *", "Dish name (Arabic) *")}</label>
            <input id={`${prefix}-name-ar`} type="text" dir="rtl"
              placeholder={add ? t("مثال: قلاية بندورة بلدي باللحمة", "e.g. قلاية بندورة بلدي باللحمة") : undefined}
              value={add ? addNameAr : editNameAr}
              onChange={(e) => (add ? setAddNameAr : setEditNameAr)(e.target.value)}
              required className="input" />
          </div>
          <div>
            <label htmlFor={`${prefix}-name-en`} className="field-label">{add ? t("اسم الطبق بالإنجليزية *", "English dish name *") : t("اسم الصنف (إنجليزي) *", "Dish name (English) *")}</label>
            <input id={`${prefix}-name-en`} type="text" dir="ltr"
              placeholder={add ? "e.g. Local Tomato Pan with Meat" : undefined}
              value={add ? addNameEn : editNameEn}
              onChange={(e) => (add ? setAddNameEn : setEditNameEn)(e.target.value)}
              required className="input" />
          </div>
        </div>

        <div className="grid grid-cols-1 items-end gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor={`${prefix}-price`} className="field-label">{t("السعر بالدينار الأردني (JOD) *", "Price in Jordanian dinars (JOD) *")}</label>
            <div className="relative" dir="ltr">
              <input id={`${prefix}-price`} type="number" step="0.05" min="0.05" dir="ltr"
                placeholder={add ? t("مثال: 3.50", "e.g. 3.50") : undefined}
                value={price}
                onChange={(e) => (add ? setAddPriceJod : setEditPriceJod)(e.target.value)}
                required className="input pe-14 text-start tabular-nums" />
              <span className="pointer-events-none absolute end-3.5 top-1/2 -translate-y-1/2 text-sm font-semibold text-subtle">JOD</span>
            </div>
            <span className="mt-1 block text-xs text-subtle">
              {add ? t("المقابل بالفلس:", "Equivalent in fils:") : t("السعر بالفلس:", "Price in fils:")} <span className="tabular-nums">{priceToFils(price) ?? "—"}</span> {t("فلس", "fils")}
            </span>
          </div>
          <div className="flex min-h-[2.875rem] items-center justify-between gap-3 rounded-[0.875rem] border border-line bg-surface-2 px-3.5 py-2 sm:mb-5">
            <span className="text-sm font-semibold text-ink-2">
              {add ? t("متوفر فوراً للطلب في الفرع", "Available to order right away") : t("متوفر حالياً للطلب", "Currently available to order")}
            </span>
            <Switch
              checked={add ? addIsAvailable : editIsAvailable}
              onChange={(next) => (add ? setAddIsAvailable : setEditIsAvailable)(next)}
              label={add ? t("حالة التوفر الأولية", "Initial availability") : t("حالة التوفر", "Availability")}
            />
          </div>
        </div>

        <div>
          <label htmlFor={`${prefix}-description-ar`} className="field-label">{add ? t("وصف الطبق والمكونات (عربي)", "Dish description & ingredients (Arabic)") : t("الوصف والمكونات (عربي)", "Description & ingredients (Arabic)")}</label>
          <textarea id={`${prefix}-description-ar`} dir="rtl"
            placeholder={add ? t("مثال: بندورة بلدية طازجة مطبوخة بزيت الزيتون البكر واللحم البلدي والصنوبر...", "e.g. بندورة بلدية طازجة مطبوخة بزيت الزيتون البكر واللحم البلدي والصنوبر...") : undefined}
            value={add ? addDescAr : editDescAr}
            onChange={(e) => (add ? setAddDescAr : setEditDescAr)(e.target.value)}
            rows={2} className="input" />
        </div>
        <div>
          <label htmlFor={`${prefix}-description-en`} className="field-label">{add ? t("وصف الطبق (إنجليزي - اختياري)", "Dish description (English, optional)") : t("الوصف (إنجليزي)", "Description (English)")}</label>
          <textarea id={`${prefix}-description-en`} dir="ltr"
            placeholder={add ? "e.g. Fresh local tomatoes cooked in virgin olive oil with tender beef and toasted pine nuts..." : undefined}
            value={add ? addDescEn : editDescEn}
            onChange={(e) => (add ? setAddDescEn : setEditDescEn)(e.target.value)}
            rows={2} className="input" />
        </div>

        <ProductImagePicker images={add ? addImages : editImages} onChange={add ? setAddImages : setEditImages} />
      </div>
    );
  };

  const errorBox = (text: string | null) => (
    <AnimatePresence>
      {text && (
        <motion.div
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: "auto", x: [0, -6, 6, -3, 3, 0] }}
          exit={{ opacity: 0, height: 0 }}
          transition={{ duration: 0.35 }}
          className="overflow-hidden"
          role="alert"
        >
          <div className="mb-4 flex items-start gap-2.5 rounded-2xl border border-danger/25 bg-danger-soft px-4 py-3 text-sm text-danger-ink">
            <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span>{text}</span>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );

  return (
    <div className="flex flex-1 flex-col" dir={dir}>
      <Toast message={feedback?.text} tone={feedback?.type === "error" ? "error" : "success"} />

      <main className="mx-auto w-full max-w-7xl flex-1 px-4 pb-28 pt-6 sm:px-6 sm:pt-8 lg:pb-12">
        {/* Page Title & Tab Navigation */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: EASE_OUT }}
          className="mb-6 flex flex-col justify-between gap-4 border-b border-line pb-5 sm:flex-row sm:items-end"
        >
          <div className="flex items-start gap-4">
            <span className="flex size-12 shrink-0 items-center justify-center rounded-2xl bg-brand text-on-brand shadow-glow" aria-hidden="true">
              <BookOpenText className="size-6" />
            </span>
            <div>
              <h1 className="font-display text-2xl font-bold text-ink sm:text-3xl">{t("إدارة المطعم وقائمة الطعام", "Restaurant & Menu")}</h1>
              <p className="mt-1 text-sm text-muted">
                {t("إدارة بيانات المطعم والفرع وأقسام القائمة وأطباقها من مكان واحد.", "Manage the restaurant and branch details, menu categories and dishes in one place.")}
              </p>
            </div>
          </div>
          <SegmentedControl
            value={activeTab}
            onChange={setActiveTab}
            ariaLabel={t("أقسام الصفحة", "Page sections")}
            stretch
            segments={[
              { value: "products", label: t("القائمة", "Menu"), count: products.length, icon: <UtensilsCrossed className="size-4" aria-hidden="true" /> },
              { value: "restaurant", label: t("المطعم", "Restaurant"), icon: <Store className="size-4" aria-hidden="true" /> },
            ]}
          />
        </motion.div>

        <AnimatePresence mode="wait" initial={false}>
          {/* ======================================================== */}
          {/* Section A: Restaurant Information Tab                     */}
          {/* ======================================================== */}
          {activeTab === "restaurant" && (
            <motion.div key="restaurant" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.35, ease: EASE_OUT }} className="mx-auto max-w-4xl">
              <Reveal
                ready={!loadingProfile || loadFailed}
                label={t("جاري تحميل بيانات المطعم...", "Loading restaurant details...")}
                skeleton={<><RestaurantFormSkeleton /><SlowNote className="mt-5" /></>}
              >
              {loadFailed ? (
                <LoadError title={t("ما قدرنا نحمّل بيانات المطعم", "We couldn't load the restaurant details")} onRetry={() => void loadData()} retrying={loadingProfile} />
              ) : (
                <form onSubmit={handleSaveRestaurant} className="space-y-5">
                  {/* Brand & Contact Card */}
                  <section className="space-y-4 rounded-[1.75rem] border border-line bg-surface p-5 shadow-card sm:p-6">
                    <h2 className="flex items-center gap-2.5 font-display text-lg font-bold text-ink">
                      <span className="flex size-9 items-center justify-center rounded-xl bg-brand-soft text-brand-soft-ink"><Store className="size-[18px]" aria-hidden="true" /></span>
                      {t("الهوية والاتصال", "Identity & contact")}
                    </h2>
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                      <div>
                        <label htmlFor="restaurant-name-ar" className="field-label">{t("اسم المطعم (عربي)", "Restaurant name (Arabic)")}</label>
                        <input id="restaurant-name-ar" type="text" dir="rtl" value={nameAr} onChange={(e) => setNameAr(e.target.value)} required className="input" />
                      </div>
                      <div>
                        <label htmlFor="restaurant-name-en" className="field-label">{t("اسم المطعم (إنجليزي)", "Restaurant name (English)")}</label>
                        <input id="restaurant-name-en" type="text" dir="ltr" value={nameEn} onChange={(e) => setNameEn(e.target.value)} required className="input" />
                      </div>
                    </div>
                    <div>
                      <label htmlFor="restaurant-phone" className="field-label">{t("هاتف المطعم الرئيسي", "Main restaurant phone")}</label>
                      <div className="relative" dir="ltr">
                        <Phone className="pointer-events-none absolute start-3.5 top-1/2 size-[18px] -translate-y-1/2 text-subtle" aria-hidden="true" />
                        <input id="restaurant-phone" type="text" dir="ltr" value={phone} onChange={(e) => setPhone(e.target.value)} required className="input ps-11 text-start" />
                      </div>
                    </div>
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                      <div>
                        <label htmlFor="restaurant-about-ar" className="field-label">{t("نبذة عن المطعم (عربي)", "About the restaurant (Arabic)")}</label>
                        <textarea id="restaurant-about-ar" dir="rtl" value={aboutAr} onChange={(e) => setAboutAr(e.target.value)} rows={3} required className="input" />
                      </div>
                      <div>
                        <label htmlFor="restaurant-about-en" className="field-label">{t("نبذة عن المطعم (إنجليزي)", "About the restaurant (English)")}</label>
                        <textarea id="restaurant-about-en" dir="ltr" value={aboutEn} onChange={(e) => setAboutEn(e.target.value)} rows={3} required className="input" />
                      </div>
                    </div>
                  </section>

                  {/* Branch Info Card */}
                  <section className="space-y-4 rounded-[1.75rem] border border-line bg-surface p-5 shadow-card sm:p-6">
                    <h2 className="flex items-center gap-2.5 font-display text-lg font-bold text-ink">
                      <span className="flex size-9 items-center justify-center rounded-xl bg-brand-soft text-brand-soft-ink"><MapPin className="size-[18px]" aria-hidden="true" /></span>
                      {t("بيانات الفرع وأوقات العمل", "Branch details & opening hours")}
                    </h2>
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                      <div>
                        <label htmlFor="branch-name-ar" className="field-label">{t("اسم الفرع (عربي)", "Branch name (Arabic)")}</label>
                        <input id="branch-name-ar" dir="rtl" value={branchNameAr} onChange={(e) => setBranchNameAr(e.target.value)} required className="input" />
                      </div>
                      <div>
                        <label htmlFor="branch-name-en" className="field-label">{t("اسم الفرع (إنجليزي)", "Branch name (English)")}</label>
                        <input id="branch-name-en" dir="ltr" value={branchNameEn} onChange={(e) => setBranchNameEn(e.target.value)} required className="input" />
                      </div>
                    </div>
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                      <div>
                        <label htmlFor="branch-address-ar" className="field-label">{t("عنوان الفرع (عربي)", "Branch address (Arabic)")}</label>
                        <input id="branch-address-ar" type="text" dir="rtl" value={branchAddressAr} onChange={(e) => setBranchAddressAr(e.target.value)} className="input" />
                      </div>
                      <div>
                        <label htmlFor="branch-address-en" className="field-label">{t("عنوان الفرع (إنجليزي)", "Branch address (English)")}</label>
                        <input id="branch-address-en" type="text" dir="ltr" value={branchAddressEn} onChange={(e) => setBranchAddressEn(e.target.value)} className="input" />
                      </div>
                    </div>
                    <div>
                      <label htmlFor="branch-phone" className="field-label">{t("هاتف الفرع المباشر", "Branch direct phone")}</label>
                      <div className="relative" dir="ltr">
                        <Phone className="pointer-events-none absolute start-3.5 top-1/2 size-[18px] -translate-y-1/2 text-subtle" aria-hidden="true" />
                        <input id="branch-phone" type="text" dir="ltr" value={branchPhone} onChange={(e) => setBranchPhone(e.target.value)} className="input ps-11 text-start" />
                      </div>
                    </div>

                    {openingHours.length > 0 && (
                      <div className="border-t border-line pt-4">
                        <h3 className="mb-3 flex items-center gap-2 text-sm font-bold text-ink">
                          <Clock3 className="size-4 text-brand" aria-hidden="true" />
                          {t("أوقات العمل (تُحفظ مع بيانات الفرع)", "Opening hours (saved with the branch details)")}
                        </h3>
                        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                          {openingHours.map((oh) => (
                            <div key={oh.day_of_week} className="rounded-2xl border border-line bg-surface-2 p-3.5">
                              <span className="mb-2 block text-sm font-bold text-brand">
                                {DAYS_MAP[oh.day_of_week]?.[lang] || t(`يوم ${oh.day_of_week}`, `Day ${oh.day_of_week}`)}
                              </span>
                              <div className="grid grid-cols-2 gap-2">
                                <label className="text-xs font-semibold text-muted">{t("من", "From")}
                                  <input type="time" value={oh.opens_at} required
                                    onChange={(e) => setOpeningHours((days) => days.map((d) => d.day_of_week === oh.day_of_week ? { ...d, opens_at: e.target.value } : d))}
                                    className="input mt-1 min-h-10 px-2.5 py-1.5 text-sm" />
                                </label>
                                <label className="text-xs font-semibold text-muted">{t("إلى", "To")}
                                  <input type="time" value={oh.closes_at} required
                                    onChange={(e) => setOpeningHours((days) => days.map((d) => d.day_of_week === oh.day_of_week ? { ...d, closes_at: e.target.value } : d))}
                                    className="input mt-1 min-h-10 px-2.5 py-1.5 text-sm" />
                                </label>
                              </div>
                              {/* Optional note for the day (the guests' assistant mentions it with the hours) */}
                              <div className="mt-2 space-y-1.5">
                                <input type="text" dir="rtl" value={oh.notes_ar ?? ""} maxLength={200}
                                  onChange={(e) => setOpeningHours((days) => days.map((d) => d.day_of_week === oh.day_of_week ? { ...d, notes_ar: e.target.value } : d))}
                                  placeholder={t("ملاحظة بالعربية (اختياري)", "Note in Arabic (optional)")}
                                  aria-label={t(`ملاحظة يوم ${DAYS_MAP[oh.day_of_week]?.ar ?? oh.day_of_week} بالعربية`, `Arabic note for ${DAYS_MAP[oh.day_of_week]?.en ?? oh.day_of_week}`)}
                                  className="input min-h-9 px-2.5 py-1.5 text-xs" />
                                <input type="text" dir="ltr" value={oh.notes_en ?? ""} maxLength={200}
                                  onChange={(e) => setOpeningHours((days) => days.map((d) => d.day_of_week === oh.day_of_week ? { ...d, notes_en: e.target.value } : d))}
                                  placeholder={t("ملاحظة بالإنجليزية (اختياري)", "Note in English (optional)")}
                                  aria-label={t(`ملاحظة يوم ${DAYS_MAP[oh.day_of_week]?.ar ?? oh.day_of_week} بالإنجليزية`, `English note for ${DAYS_MAP[oh.day_of_week]?.en ?? oh.day_of_week}`)}
                                  className="input min-h-9 px-2.5 py-1.5 text-xs" />
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </section>

                  <div className="flex justify-end">
                    <button type="submit" disabled={savingProfile} className="btn btn-primary btn-lg w-full sm:w-auto">
                      {savingProfile ? <LoaderCircle className="size-5 animate-spin" aria-hidden="true" /> : <Save className="size-5" aria-hidden="true" />}
                      {savingProfile ? t("جاري الحفظ...", "Saving...") : t("حفظ التغييرات", "Save changes")}
                    </button>
                  </div>
                </form>
              )}
              </Reveal>
            </motion.div>
          )}

          {/* ======================================================== */}
          {/* Section B: Menu Products Catalog & Availability Tab      */}
          {/* ======================================================== */}
          {activeTab === "products" && (
            <motion.div key="products" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.35, ease: EASE_OUT }}>
              <CategoriesEditor onSaved={refreshCategories} />

              {/* Toolbar: categories, search and "add a dish" */}
              <div className="mb-5 flex flex-col gap-3 rounded-[1.5rem] border border-line bg-surface p-3 shadow-card lg:flex-row lg:items-center lg:justify-between">
                <div className="no-scrollbar fade-x -mx-1 flex items-center gap-1.5 overflow-x-auto px-1" role="group" aria-label={t("تصفية حسب التصنيف", "Filter by category")}>
                  {categoryTabs.map((tab) => {
                    const active = selectedCategory === tab.id;
                    return (
                      <button
                        key={tab.id ?? "all"}
                        type="button"
                        onClick={() => setSelectedCategory(tab.id)}
                        aria-pressed={active}
                        className={`relative isolate flex h-10 shrink-0 items-center gap-1.5 rounded-full px-4 text-sm font-semibold transition-colors ${active ? "text-on-brand" : "text-ink-2 hover:bg-surface-3"}`}
                      >
                        {active && <motion.span layoutId="admin-menu-category" className="absolute inset-0 -z-10 rounded-full bg-brand shadow-glow" transition={spring.snappy} />}
                        <span>{tab.label}</span>
                        <span className={`text-xs tabular-nums ${active ? "text-on-brand/80" : "text-subtle"}`}>{tab.count}</span>
                      </button>
                    );
                  })}
                </div>

                <div className="flex flex-wrap items-center gap-2 sm:flex-nowrap">
                  <div className="relative flex-1 sm:w-72 sm:flex-none">
                    <Search className="pointer-events-none absolute start-3.5 top-1/2 size-[18px] -translate-y-1/2 text-subtle" aria-hidden="true" />
                    <input
                      type="text"
                      placeholder={t("بحث باسم الصنف...", "Search by dish name...")}
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      aria-label={t("بحث باسم الصنف", "Search by dish name")}
                      className="input min-h-10 rounded-full ps-11 pe-10"
                    />
                    {searchQuery && (
                      <button onClick={() => setSearchQuery("")} aria-label={t("مسح البحث", "Clear search")} className="absolute end-2 top-1/2 flex size-7 -translate-y-1/2 items-center justify-center rounded-full text-subtle hover:bg-surface-3 hover:text-ink">
                        <X className="size-4" aria-hidden="true" />
                      </button>
                    )}
                  </div>
                  <motion.button whileTap={{ scale: 0.96 }} onClick={openAddModal} className="btn btn-primary btn-sm h-10 rounded-full px-4">
                    <Plus className="size-4" aria-hidden="true" />
                    <span>{t("إضافة طبق جديد", "Add new dish")}</span>
                  </motion.button>
                </div>
              </div>

              <div className="mb-4 flex justify-end">
                <div className="inline-flex items-center gap-2 rounded-full border border-brand-line bg-brand-soft px-3.5 py-2 text-sm" aria-live="polite">
                  <span className="size-2 rounded-full bg-success" aria-hidden="true" />
                  <span className="font-semibold text-brand-soft-ink">{t("متوفر", "Available")}</span>
                  <span className="font-bold tabular-nums text-ink">{availableCount}</span>
                  <span className="text-muted">{t("من أصل", "of")}</span>
                  <span className="font-bold tabular-nums text-ink">{products.length}</span>
                  <span className="text-muted">{t("منتج", "dishes")}</span>
                </div>
              </div>

              <Reveal
                ready={!loadingProducts || loadFailed}
                label={t("جاري تحميل قائمة الأصناف...", "Loading dishes...")}
                skeleton={<><AdminDishesSkeleton /><SlowNote className="mt-5" /></>}
              >
              {loadFailed ? (
                <LoadError title={t("ما قدرنا نحمّل الأصناف", "We couldn't load the dishes")} onRetry={() => void loadData()} retrying={loadingProducts} />
              ) : filteredProducts.length === 0 ? (
                <EmptyState
                  icon={<UtensilsCrossed className="size-7" strokeWidth={1.75} aria-hidden="true" />}
                  title={t("لا توجد أصناف مطابقة", "No matching dishes")}
                  description={t("لا توجد أصناف تطابق معايير البحث الحالية.", "No dishes match the current search.")}
                  action={
                    <button onClick={openAddModal} className="btn btn-soft">
                      <Plus className="size-4" aria-hidden="true" />
                      {t("إضافة طبق جديد في هذا التصنيف", "Add a new dish to this category")}
                    </button>
                  }
                />
              ) : (
                <div className="space-y-8">
                  {categories.filter((category) => filteredProducts.some((product) => product.category_id === category.id)).map((category) => (
                    <section key={category.id}>
                      <div className="mb-3 flex items-baseline gap-2 border-b border-line pb-2">
                        <h2 className="font-display text-lg font-bold text-ink">{localized(category.name_ar, category.name_en)}</h2>
                        <span className="text-sm text-subtle">({filteredProducts.filter((product) => product.category_id === category.id).length})</span>
                      </div>
                      <motion.div layout className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                        <AnimatePresence initial={false}>
                          {filteredProducts.filter((product) => product.category_id === category.id).map((prod, index) => {
                            // Main name in the current language; the other language's name underneath.
                            const displayName = localized(prod.name_ar, prod.name_en);
                            const otherName = displayName === prod.name_ar ? prod.name_en : prod.name_ar;
                            const description = localized(prod.description_ar, prod.description_en);
                            const photo = prod.images?.[0]?.url || prod.image_asset_url;
                            return (
                              <motion.article
                                key={prod.id}
                                layout
                                initial={{ opacity: 0, y: 14, scale: 0.98 }}
                                animate={{ opacity: 1, y: 0, scale: 1 }}
                                exit={{ opacity: 0, scale: 0.94, transition: { duration: 0.18 } }}
                                transition={{ ...spring.smooth, delay: Math.min(index, 8) * 0.03 }}
                                className={`group flex flex-col gap-3 rounded-[1.5rem] border bg-surface p-4 shadow-card transition-shadow hover:shadow-lift ${!prod.is_available ? "border-danger/25" : "border-line"}`}
                              >
                                <div className="flex gap-3.5">
                                  <ProductVisual url={photo} alt={displayName} className={`size-20 shrink-0 rounded-2xl ${!prod.is_available ? "grayscale" : ""}`} iconClassName="size-7" />
                                  <div className="min-w-0 flex-1">
                                    <div className="flex items-start justify-between gap-2">
                                      <div className="min-w-0">
                                        <h3 className="line-clamp-2 font-bold leading-snug text-ink">{displayName}</h3>
                                        <span className="block truncate text-end text-xs text-subtle" dir="auto">{otherName}</span>
                                      </div>
                                      <div className="shrink-0 text-end">
                                        <span className="block font-display text-lg font-bold leading-none tabular-nums text-brand">{localized(prod.price_display_ar, prod.price_display_en)}</span>
                                        <span className="mt-1 block text-[0.6875rem] tabular-nums text-subtle">{prod.price_minor} {t("فلس", "fils")}</span>
                                      </div>
                                    </div>
                                    {description && <p className="mt-1.5 line-clamp-2 text-sm leading-relaxed text-muted">{description}</p>}
                                  </div>
                                </div>

                                {/* Instant availability (REQ-024), edit and delete */}
                                <div className="mt-auto flex items-center justify-between gap-2 border-t border-line pt-3">
                                  <div className="flex items-center gap-2.5">
                                    <Switch
                                      checked={prod.is_available}
                                      busy={togglingId === prod.id}
                                      onChange={(_, event) => handleToggleAvailability(prod, event)}
                                      label={prod.is_available ? t("انقر للتعطيل الفوري", "Click to mark unavailable now") : t("انقر للتفعيل الفوري", "Click to mark available now")}
                                      size="sm"
                                    />
                                    <span className={`text-sm font-semibold ${prod.is_available ? "text-success" : "text-danger"}`}>
                                      {prod.is_available ? t("متوفر", "Available") : t("غير متوفر", "Unavailable")}
                                    </span>
                                  </div>
                                  <div className="flex items-center gap-1">
                                    <button onClick={() => openEditModal(prod)} className="btn btn-ghost btn-sm min-h-9 px-3" title={t("تعديل اسم أو سعر أو وصف الطبق", "Edit the dish name, price or description")}>
                                      <Pencil className="size-4" aria-hidden="true" />
                                      {t("تعديل", "Edit")}
                                    </button>
                                    <button onClick={(e) => openDeleteModal(prod, e)} className="flex size-9 items-center justify-center rounded-xl text-subtle transition-colors hover:bg-danger-soft hover:text-danger" title={t("حذف الصنف نهائياً", "Delete the dish permanently")} aria-label={t("حذف", "Delete")}>
                                      <Trash2 className="size-4" aria-hidden="true" />
                                    </button>
                                  </div>
                                </div>
                              </motion.article>
                            );
                          })}
                        </AnimatePresence>
                      </motion.div>
                    </section>
                  ))}
                </div>
              )}
              </Reveal>
            </motion.div>
          )}
        </AnimatePresence>
      </main>

      {/* Add a dish */}
      <Sheet open={isAddModalOpen} onClose={() => setIsAddModalOpen(false)} labelledBy="add-dish-title" size="lg">
        <SheetHeader
          id="add-dish-title"
          icon={<Plus className="size-6" aria-hidden="true" />}
          title={t("إضافة طبق جديد للقائمة", "Add a new dish to the menu")}
          subtitle={t("سيتم تسجيل بيانات الطبق ومزامنتها فورياً مع شاشات الزبائن والمساعد الذكي.", "The dish will be saved and synced right away to customer screens and the AI assistant.")}
          onClose={() => setIsAddModalOpen(false)}
        />
        <SheetBody>
          {errorBox(addError)}
          <form id="add-dish-form" onSubmit={handleCreateProduct}>{dishFields("add")}</form>
        </SheetBody>
        <SheetFooter className="justify-end">
          <button type="button" onClick={() => setIsAddModalOpen(false)} className="btn btn-secondary">{t("إلغاء", "Cancel")}</button>
          <button type="submit" form="add-dish-form" disabled={savingNewProduct} className="btn btn-primary">
            {savingNewProduct ? <LoaderCircle className="size-5 animate-spin" aria-hidden="true" /> : <Plus className="size-5" aria-hidden="true" />}
            {savingNewProduct ? t("جاري الإضافة...", "Adding...") : t("إضافة الصنف الآن", "Add dish now")}
          </button>
        </SheetFooter>
      </Sheet>

      {/* Edit a dish */}
      <Sheet open={editingProduct !== null} onClose={() => setEditingProduct(null)} labelledBy="edit-dish-title" size="lg">
        <SheetHeader
          id="edit-dish-title"
          icon={<Pencil className="size-6" aria-hidden="true" />}
          title={t("تعديل بيانات الطبق", "Edit dish")}
          subtitle={t("التعديل يسري مباشرة ويلغي القيم السابقة فوراً في شاشات الزبائن والمساعد الذكي.", "Changes take effect right away and replace the previous values on customer screens and in the AI assistant.")}
          onClose={() => setEditingProduct(null)}
        />
        <SheetBody>
          {errorBox(editError)}
          {editingProduct && <form id="edit-dish-form" onSubmit={handleSaveProduct}>{dishFields("edit")}</form>}
        </SheetBody>
        <SheetFooter className="justify-between">
          <button
            type="button"
            onClick={(e) => {
              const target = editingProduct;
              if (!target) return;
              setEditingProduct(null);
              openDeleteModal(target, e);
            }}
            className="btn btn-danger-soft"
          >
            <Trash2 className="size-4" aria-hidden="true" />
            {t("حذف", "Delete")}
          </button>
          <div className="flex items-center gap-2">
            <button type="button" onClick={() => setEditingProduct(null)} className="btn btn-secondary">{t("إلغاء", "Cancel")}</button>
            <button type="submit" form="edit-dish-form" disabled={savingProduct} className="btn btn-primary">
              {savingProduct ? <LoaderCircle className="size-5 animate-spin" aria-hidden="true" /> : <Save className="size-5" aria-hidden="true" />}
              {savingProduct ? t("جاري الحفظ...", "Saving...") : t("حفظ التعديلات", "Save changes")}
            </button>
          </div>
        </SheetFooter>
      </Sheet>

      {/* Delete a dish */}
      <Sheet open={deletingProduct !== null} onClose={() => { if (!isDeleting) setDeletingProduct(null); }} labelledBy="delete-dish-title" size="sm" role="alertdialog">
        {deletingProduct && (
          <>
            <SheetHeader
              id="delete-dish-title"
              icon={<TriangleAlert className="size-6" aria-hidden="true" />}
              title={t("تأكيد حذف الصنف", "Confirm dish deletion")}
              subtitle={<span className="font-semibold text-danger-ink">{t("إلغاء نهائي من القائمة", "Permanent removal from the menu")}</span>}
            />
            <SheetBody>
              <p className="text-[0.9375rem] leading-relaxed text-ink-2">
                {t("هل أنت متأكد من رغبتك في حذف طبق", "Are you sure you want to permanently delete")}{" "}
                <strong className="font-bold text-ink">&quot;{localized(deletingProduct.name_ar, deletingProduct.name_en)}&quot;</strong>{t(" نهائياً؟", "?")}
              </p>
              <p className="mt-3 flex items-start gap-2 rounded-2xl border border-warning/30 bg-warning-soft p-3 text-sm leading-relaxed text-warning-ink">
                <Info className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                {t("سيتم إزالة الصنف فورياً من شاشات الزبائن ونظام الطلبات والمساعد الذكي، ولن يتمكن أي زبون من طلبه مجدداً. السجلات المحاسبية للطلبات السابقة ستبقى محفوظة بأمان.", "The dish will be removed right away from customer screens, the ordering system and the AI assistant, and no customer will be able to order it again. Accounting records of past orders stay safely stored.")}
              </p>
            </SheetBody>
            <SheetFooter className="justify-end">
              <button type="button" onClick={() => setDeletingProduct(null)} disabled={isDeleting} className="btn btn-secondary">
                {t("تراجع وإلغاء", "Go back")}
              </button>
              <button type="button" onClick={handleDeleteProduct} disabled={isDeleting} className="btn btn-danger">
                {isDeleting ? <LoaderCircle className="size-5 animate-spin" aria-hidden="true" /> : <Trash2 className="size-4" aria-hidden="true" />}
                {isDeleting ? t("جاري الحذف...", "Deleting...") : t("نعم، حذف الصنف", "Yes, delete dish")}
              </button>
            </SheetFooter>
          </>
        )}
      </Sheet>
    </div>
  );
}
