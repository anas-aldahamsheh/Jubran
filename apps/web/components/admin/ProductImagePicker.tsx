"use client";

import { AnimatePresence, motion } from "motion/react";
import { ImagePlus, X } from "lucide-react";
import { getApiAssetUrl } from "@/lib/api";
import { spring } from "@/lib/motion";
import { useGlobalDialog } from "@/components/common/GlobalDialogProvider";
import { useLanguage } from "@/context/LanguageContext";

export interface ManagedProductImage {
  key: string;
  url: string;
  id?: string;
  file?: File;
}

// A per-page counter: crypto.randomUUID() only exists on HTTPS or localhost, and the
// admin panel is also opened over plain HTTP on the restaurant's network (a tablet).
let nextImageKey = 0;
const newImageKey = () => `new-${Date.now().toString(36)}-${(nextImageKey++).toString(36)}`;

interface ProductImagePickerProps {
  images: ManagedProductImage[];
  onChange: (images: ManagedProductImage[]) => void;
}

export function ProductImagePicker({ images, onChange }: ProductImagePickerProps) {
  const { showAlert } = useGlobalDialog();
  const { t } = useLanguage();
  const addFiles = (selected: FileList | null) => {
    if (!selected?.length) return;
    const remaining = 5 - images.length;
    if (selected.length > remaining) {
      void showAlert(t(`يمكن إضافة ${remaining} صورة فقط للوصول إلى الحد الأقصى وهو 5 صور.`, `Only ${remaining} more ${remaining === 1 ? "photo" : "photos"} can be added (5 photos at most).`));
    }
    const accepted = Array.from(selected).slice(0, remaining);
    const invalidType = accepted.find((file) => !["image/jpeg", "image/png", "image/webp", "image/gif"].includes(file.type));
    if (invalidType) {
      void showAlert(t("أنواع الصور المدعومة: JPG وPNG وWEBP وGIF.", "Supported photo types: JPG, PNG, WEBP and GIF."));
      return;
    }
    const tooLarge = accepted.find((file) => file.size > 8 * 1024 * 1024);
    if (tooLarge) {
      void showAlert(t("يجب ألا يتجاوز حجم الصورة الواحدة 8 ميغابايت.", "Each photo must be 8 MB or smaller."));
      return;
    }
    onChange([...images, ...accepted.map((file) => ({
      key: newImageKey(),
      url: URL.createObjectURL(file),
      file,
    }))]);
  };

  const removeImage = (image: ManagedProductImage) => {
    if (image.file) URL.revokeObjectURL(image.url);
    onChange(images.filter((item) => item.key !== image.key));
  };

  return (
    <div>
      <p className="field-label">{t("صور الطبق (حتى 5 صور)", "Dish photos (up to 5)")}</p>
      <div className="flex flex-wrap gap-2.5">
        <AnimatePresence initial={false}>
          {images.map((image, index) => (
            <motion.div
              key={image.key}
              layout
              initial={{ opacity: 0, scale: 0.8 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.8 }}
              transition={spring.snappy}
              className="group relative size-24 overflow-hidden rounded-2xl border border-line bg-surface-2 shadow-card"
            >
              {/* eslint-disable-next-line @next/next/no-img-element -- new photos are local previews (blob:) and saved ones come from the API server */}
              <img src={getApiAssetUrl(image.url)} alt={t(`صورة الطبق ${index + 1}`, `Dish photo ${index + 1}`)} className="h-full w-full object-cover" />
              {index === 0 && <span className="absolute bottom-1.5 start-1.5 rounded-full bg-black/60 px-2 py-0.5 text-[0.625rem] font-bold text-white">{t("الرئيسية", "Main")}</span>}
              <button
                type="button"
                onClick={() => removeImage(image)}
                aria-label={t(`حذف صورة الطبق ${index + 1}`, `Remove dish photo ${index + 1}`)}
                className="absolute end-1.5 top-1.5 flex size-7 items-center justify-center rounded-full bg-surface/95 text-danger shadow-card transition-transform hover:scale-110"
              >
                <X className="size-4" aria-hidden="true" />
              </button>
            </motion.div>
          ))}
        </AnimatePresence>
        {images.length < 5 && (
          <motion.label layout whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }} className="flex size-24 cursor-pointer flex-col items-center justify-center gap-1.5 rounded-2xl border-2 border-dashed border-brand-line bg-brand-soft/50 text-center text-xs font-semibold text-brand-soft-ink transition-colors hover:bg-brand-soft">
            <ImagePlus className="size-6" aria-hidden="true" />
            <span>{t("إضافة صورة", "Add photo")}</span>
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp,image/gif"
              multiple
              className="sr-only"
              onChange={(event) => {
                addFiles(event.target.files);
                event.currentTarget.value = "";
              }}
            />
          </motion.label>
        )}
      </div>
      <p className="mt-2 text-xs text-subtle">{t("تُحفظ الصور الجديدة وتُحذف الصور التي أزلتها عند حفظ الطبق.", "New photos are saved, and removed ones deleted, when you save the dish.")}</p>
    </div>
  );
}
