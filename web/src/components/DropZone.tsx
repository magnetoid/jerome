import { useRef, useState } from "react";
import { UploadIcon } from "lucide-react";
import { cn } from "@/lib/utils";

const OK = /\.(docx|epub|pdf)$/i;

interface Props {
  file: File | null;
  onFile: (file: File) => void;
  onReject: (message: string) => void;
}

export default function DropZone({ file, onFile, onReject }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  function take(f: File | undefined) {
    if (!f) return;
    if (!OK.test(f.name)) {
      onReject("Choose a .docx, .epub or .pdf file.");
      return;
    }
    onFile(f);
  }

  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        aria-label="Choose a file or drop it here"
        data-over={over ? "true" : undefined}
        className={cn(
          "group flex cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border-2 border-dashed p-8 text-center outline-none transition-[transform,background-color,border-color] duration-150 hover:border-foreground/30 hover:bg-muted/50 focus-visible:border-brand focus-visible:ring-[3px] focus-visible:ring-ring",
          over && "scale-[1.01] border-brand bg-brand/10 hover:border-brand hover:bg-brand/10",
        )}
        onClick={() => input.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            input.current?.click();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          take(e.dataTransfer.files?.[0]);
        }}
      >
        <UploadIcon
          aria-hidden
          className={cn(
            "mb-1 size-6 text-muted-foreground transition-[transform,color] duration-150 group-hover:text-foreground",
            over && "-translate-y-1 text-foreground",
          )}
        />
        {file ? (
          <span className="font-medium">{file.name}</span>
        ) : (
          <>
            <span className="font-medium">Drop a DOCX, EPUB or PDF here</span>
            <span className="text-sm text-muted-foreground">or click to choose a file</span>
          </>
        )}
      </div>
      <input
        ref={input}
        type="file"
        accept=".docx,.epub,.pdf"
        aria-label="Book file"
        className="sr-only"
        tabIndex={-1}
        onChange={(e) => {
          take(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
    </div>
  );
}
