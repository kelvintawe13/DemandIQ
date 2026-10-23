import { useEffect, useRef, useState } from 'react';
import { UploadIcon, InfoIcon, LightbulbIcon } from 'lucide-react';
import { CameraNotebook } from './illustrations/CameraNotebook';
import { EmptyState } from './ui/EmptyState';
import { Spinner } from './ui/LoadingState';
import { useToast } from '../contexts/ToastContext';
import { api, ApiException } from '../api/client';

interface UploadImagePanelProps {
  imageName?: string;
  jobId?: string | null;
  onUploaded: (jobId: string, fileName: string) => void;
}

const MAX_BYTES = 5 * 1024 * 1024;
const SUPPORTED_FORMATS = 'JPG, PNG, WebP, GIF, BMP or TIFF';
const SUPPORTED_IMAGE_TYPES = new Set([
  'image/jpeg',
  'image/png',
  'image/webp',
  'image/gif',
  'image/bmp',
  'image/tiff',
]);
const SUPPORTED_EXTENSIONS = /\.(jpe?g|png|webp|gif|bmp|tiff?)$/i;
const HEIC_EXTENSIONS = /\.(heic|heif)$/i;
const HEIC_TYPES = new Set(['image/heic', 'image/heif', 'image/heic-sequence', 'image/heif-sequence']);

/** HEIC/HEIF is sniffed by the backend but Pillow cannot decode it without a plugin. */
function isHeic(file: File) {
  return HEIC_TYPES.has(file.type.toLowerCase()) || HEIC_EXTENSIONS.test(file.name);
}

function isSupportedImage(file: File) {
  return SUPPORTED_IMAGE_TYPES.has(file.type.toLowerCase()) || SUPPORTED_EXTENSIONS.test(file.name);
}

export function UploadImagePanel({ imageName, jobId, onUploaded }: UploadImagePanelProps) {
  const fileInput = useRef<HTMLInputElement>(null);
  const { toast } = useToast();
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<{ message: string; reference?: string | null; offline?: boolean } | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  const clearPreview = () => {
    setPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
  };

  const handleFile = async (file?: File) => {
    if (!file) return;
    setError(null);
    if (isHeic(file)) {
      clearPreview();
      setError({ message: 'HEIC/HEIF photos are not supported yet. Convert the photo to JPG or PNG, then upload it again.' });
      return;
    }
    if (!isSupportedImage(file)) {
      clearPreview();
      setError({ message: `Unsupported image format. Use ${SUPPORTED_FORMATS}.` });
      return;
    }
    if (file.size > MAX_BYTES) {
      clearPreview();
      setError({ message: `Image exceeds 5 MiB limit (${(file.size / 1024 / 1024).toFixed(1)} MiB)` });
      return;
    }
    // Local thumbnail preview
    try {
      const url = URL.createObjectURL(file);
      setPreviewUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return url;
      });
    } catch {}
    setUploading(true);
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await api.postForm<{ job_id: string; status: string; filename: string }>('/api/v1/ingest/image', form);
      onUploaded(res.job_id, file.name);
      /* No row count is claimed here on purpose. `POST /ingest/image` returns
         only a job id; the extracted rows are fetched separately by
         PreviewPanel, which reports how many actually came back. This toast
         used to read "2 rows extracted (demo)" — a hardcoded number, so it
         promised two rows even for a photo the OCR engine read nothing from. */
      toast(`${file.name} uploaded. Reading the photo for rows…`, 'success');
    } catch (e: unknown) {
      if (e instanceof ApiException) {
        const isParse = e.error.code === 'parse_error' || e.error.message.toLowerCase().includes('parse');
        setError({
          message: isParse ? `Parse error: ${e.error.message}` : e.error.message,
          reference: e.reference ?? undefined,
          offline: e.error.message.toLowerCase().includes('failed to fetch')
        });
      } else if (e instanceof Error && (e.message.includes('Failed to fetch') || e.message.includes('NetworkError'))) {
        setError({ message: 'Backend unreachable. Data saved locally will sync when online.', offline: true });
      } else {
        setError({ message: e instanceof Error ? e.message : 'Image upload failed' });
      }
    } finally {
      setUploading(false);
    }
  };

  const hasImage = Boolean(imageName || jobId);
  const isParseError = Boolean(error?.message.toLowerCase().includes('parse'));

  // Cancelling from the preview panel clears the parent's job state; reset the
  // local thumbnail and file input too, otherwise the card keeps showing the
  // discarded photo with the "Upload photo" copy around it.
  useEffect(() => {
    if (imageName || jobId) return;
    setPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    if (fileInput.current) fileInput.current.value = '';
  }, [imageName, jobId]);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  return (
    <section
      aria-labelledby="upload-image-heading"
      className="flex min-w-0 flex-col rounded-xl border border-slate-200 bg-white p-3 sm:p-3.5"
    >
      <h2 id="upload-image-heading" className="text-h3 font-bold tracking-tight text-brand-700">
        Upload Image
      </h2>
      <p className="mt-1 text-control text-slate-500">Upload a clear photo of handwritten visitor records.</p>

      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const f = e.dataTransfer.files?.[0];
          if (f) void handleFile(f);
        }}
        className={[
          'mt-4 flex flex-col items-center rounded-xl border-2 border-dashed px-4 py-6 text-center transition-colors sm:mt-5 sm:px-6 sm:py-7',
          dragOver ? 'border-brand-400 bg-brand-50' : 'border-brand-200 bg-brand-50/50',
          hasImage ? 'border-solid' : '',
        ].join(' ')}
      >
        {previewUrl ? (
          <img src={previewUrl} alt={imageName ?? 'Preview'} className="h-32 w-auto max-w-full rounded-lg border border-slate-200 object-contain shadow-sm sm:h-40" />
        ) : (
          <CameraNotebook className="h-[92px] w-auto max-w-full text-brand-600 sm:h-[120px]" />
        )}
        <h3 className="mt-4 text-lead font-semibold text-slate-900 sm:mt-5 sm:text-h4">
          {imageName ?? 'Upload photo of handwritten records'}
        </h3>
        <p className="mt-1.5 max-w-[320px] text-control leading-relaxed text-slate-500">
          {hasImage ? 'Photo uploaded. The rows found in it are listed in the preview — check each one against the photo before importing.' : 'Drag & drop or upload. Your photo is uploaded securely and read for dates and visitor counts.'}
        </p>
        <input
          ref={fileInput}
          type="file"
          accept="image/jpeg,image/png,image/webp,image/gif,image/bmp,image/tiff,.jpg,.jpeg,.png,.webp,.gif,.bmp,.tif,.tiff"
          capture="environment"
          className="sr-only"
          aria-label="Handwritten record photo"
          onChange={(event) => {
            const file = event.target.files?.[0];
            // Clear the value so picking the same file again after a failed
            // upload still fires onChange and can be retried.
            event.target.value = '';
            void handleFile(file);
          }}
        />
        <button
          type="button"
          onClick={() => fileInput.current?.click()}
          disabled={uploading}
          className="mt-5 inline-flex items-center gap-2.5 rounded-lg bg-brand-700 px-5 py-3 text-body font-semibold text-white shadow-sm transition-colors duration-150 ease-out hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          <UploadIcon className="h-[18px] w-[18px]" strokeWidth={2} />
          {uploading ? 'Uploading…' : hasImage ? 'Choose a different image' : 'Choose Image'}
        </button>
        {uploading && <span className="mt-3 flex items-center gap-2 text-body-xs text-slate-600"><Spinner tone="muted" className="h-4 w-4" />Processing image…</span>}
      </div>

      <p className="mt-4 flex flex-wrap items-center justify-center gap-x-2 gap-y-1 text-center text-body-xs text-slate-500">
        <InfoIcon className="h-4 w-4 shrink-0 text-slate-500" strokeWidth={1.75} />
        Accepted formats: {SUPPORTED_FORMATS} • Max size: 5 MiB
      </p>

      {!hasImage && (
        <div className="mt-4">
          <EmptyState size="sm" variant="empty" title="No image yet" description="Upload an image to start OCR extraction." />
        </div>
      )}

      {error && (
        <div className="mt-4">
          <EmptyState
            size="sm"
            variant={error.offline ? 'offline' : 'error'}
            title={isParseError ? 'Parse error' : error.offline ? 'Backend offline' : 'Image upload failed'}
            description={error.message + (error.reference ? ` (ref ${error.reference})` : '')}
            hints={
              isParseError
                ? ['Check image clarity and try again', error.reference ? `Reference ${error.reference}` : undefined].filter(Boolean) as string[]
                : error.offline
                  ? ['Backend unreachable. Data saved locally will sync when online.']
                  : error.reference
                    ? [`Reference ${error.reference}`]
                    : undefined
            }
            action={{ label: 'Retry', onClick: () => fileInput.current?.click() }}
          />
        </div>
      )}

      <div className="mt-4 flex items-start gap-2.5 rounded-lg border border-brand-100 bg-brand-50 px-3.5 py-3">
        <LightbulbIcon className="mt-0.5 h-[18px] w-[18px] shrink-0 text-brand-600" strokeWidth={1.75} />
        <p className="text-body-sm leading-relaxed text-slate-700">
          <span className="font-semibold">Tip:</span> Ensure the handwriting is clear and the date and visitor count are legible. Dates can be YYYY-MM-DD, DD/MM/YYYY or MM/DD/YYYY. An unambiguous date such as 12/31 sets the order for the whole photo; otherwise ambiguous numeric dates are day-first. iPhone HEIC/HEIF photos must be converted to JPG first.
        </p>
      </div>
    </section>
  );
}
