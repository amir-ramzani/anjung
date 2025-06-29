import { useInfiniteQuery, useMutation, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError } from "../api/client";

interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

/** Cursor pagination as one flat list plus a "load more" handle. */
export function usePaged<T>(key: QueryKey, fetchPage: (cursor: string | undefined) => Promise<Page<T>>, enabled = true) {
  const q = useInfiniteQuery({
    queryKey: key,
    queryFn: ({ pageParam }) => fetchPage(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled,
  });
  return {
    items: q.data?.pages.flatMap((p) => p.items) ?? [],
    isLoading: q.isLoading,
    error: q.error,
    hasMore: !!q.hasNextPage,
    loadingMore: q.isFetchingNextPage,
    loadMore: () => void q.fetchNextPage(),
    refetch: () => q.refetch(),
  };
}

/** A mutation that invalidates the given query keys and exposes field errors from the contract's envelope. */
export function useAction<V, R>(fn: (v: V) => Promise<R>, invalidate: QueryKey[] = [], onDone?: (r: R) => void) {
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: fn,
    onSuccess: async (r) => {
      await Promise.all(invalidate.map((k) => qc.invalidateQueries({ queryKey: k })));
      onDone?.(r);
    },
  });
  const err = m.error instanceof ApiError ? m.error : null;
  return { run: m.mutate, runAsync: m.mutateAsync, pending: m.isPending, error: m.error, fields: err?.fieldErrors ?? {}, reset: m.reset };
}

export function useForm<T extends Record<string, string | boolean>>(initial: T) {
  const [v, setV] = useState<T>(initial);
  return { v, set: <K extends keyof T>(k: K, val: T[K]) => setV((o) => ({ ...o, [k]: val })), reset: () => setV(initial), setAll: setV };
}
