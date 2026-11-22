import { useRef, useState } from "react";
import { cn } from "@/lib/utils";

const OK = /\.(docx|epub)$/i;

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
      onReject("Choose a .docx or .epub file.");
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
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border-2 border-dashed p-8 text-center outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
          over && "border-primary bg-accent",
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
        {file ? (
          <span className="font-medium">{file.name}</span>
        ) : (
          <>
            <span className="font-medium">Drop a DOCX or EPUB here</span>
            <span className="text-sm text-muted-foreground">or click to choose a file</span>
          </>
        )}
      </div>
      <input
        ref={input}
        type="file"
        accept=".docx,.epub"
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
