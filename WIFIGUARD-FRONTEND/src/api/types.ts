/** 훅 반환 형태 — 실API(TanStack Query)와 목업 훅이 같은 모양을 돌려준다. */

export interface QueryResultLike<T> {
  data: T | undefined;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => Promise<unknown>;
}

export interface MutationLike<TVars, TResult = void> {
  mutateAsync: (vars: TVars) => Promise<TResult>;
  isPending: boolean;
}

export function ready<T>(data: T): QueryResultLike<T> {
  return { data, isLoading: false, isError: false, error: null, refetch: async () => data };
}

export function syncMutation<TVars, TResult = void>(
  fn: (vars: TVars) => TResult,
): MutationLike<TVars, TResult> {
  return { mutateAsync: async (vars) => fn(vars), isPending: false };
}
