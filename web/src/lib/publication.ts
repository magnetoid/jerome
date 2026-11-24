import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { jobsKey, type Job } from "@/lib/jobs";

/** GET /v1/books/{id}/publication: what the finished EPUB and PDF say about the book. */
export interface Publication {
  title: string;
  language: string;
  authors: string[];
  translator: string | null;
  publisher: string | null;
  isbn_epub: string | null;
  isbn_pdf: string | null;
  year: number | null;
  rights: string;
  description: string | null;
  trim_size: string;
  has_cover: boolean;
}

/** The fields PUT /v1/books/{id}/publication takes; null clears one. */
export type PublicationValues = {
  [K in keyof Omit<Publication, "title" | "language" | "has_cover">]?: Publication[K] | null;
};

export type PublishFormat = "epub-book" | "pdf-book";

/** The PDF's page sizes, as the server names them. */
export const TRIM_SIZES = [
  { value: "6x9in", label: "6 × 9 in (trade paperback)" },
  { value: "5.5x8.5in", label: "5.5 × 8.5 in" },
  { value: "5x8in", label: "5 × 8 in" },
  { value: "a5", label: "A5" },
];

export const publicationKey = (id: string) => ["publication", id] as const;

export function usePublication(bookId: string) {
  return useQuery({
    queryKey: publicationKey(bookId),
    queryFn: () => apiFetch<Publication>(`/v1/books/${bookId}/publication`),
  });
}

function useSetPublication(bookId: string) {
  const client = useQueryClient();
  return (p: Publication) => client.setQueryData(publicationKey(bookId), p);
}

export function useSavePublication(bookId: string) {
  const set = useSetPublication(bookId);
  return useMutation({
    mutationFn: (values: PublicationValues) =>
      apiFetch<Publication>(`/v1/books/${bookId}/publication`, { method: "PUT", json: values }),
    onSuccess: set,
  });
}

export function useUploadCover(bookId: string) {
  const set = useSetPublication(bookId);
  return useMutation({
    mutationFn: (file: File) => {
      const body = new FormData();
      body.append("file", file);
      return apiFetch<Publication>(`/v1/books/${bookId}/cover`, { method: "PUT", body });
    },
    onSuccess: set,
  });
}

export function useDeleteCover(bookId: string) {
  const set = useSetPublication(bookId);
  return useMutation({
    mutationFn: () => apiFetch<Publication>(`/v1/books/${bookId}/cover`, { method: "DELETE" }),
    onSuccess: set,
  });
}

/** Start a publish job; the jobs list then shows it, and its file once it is done. */
export function usePublish(bookId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (format: PublishFormat) =>
      apiFetch<Job>(`/v1/books/${bookId}/publish`, { method: "POST", json: { format } }),
    onSuccess: () => void client.invalidateQueries({ queryKey: jobsKey(bookId) }),
  });
}
