import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useToast } from "../components/ui/Toast";
import { errorMessage } from "./api";

// A state-changing call with a toast for the outcome and a refresh of the data it affects.
export function useAction<TArgs, TResult>(
  fn: (args: TArgs) => Promise<TResult>,
  options: { success?: string | ((r: TResult) => string); invalidate?: string[][]; onSuccess?: (r: TResult) => void; errorTitle?: string } = {},
) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: fn,
    onSuccess: (r) => {
      if (options.success) toast("success", typeof options.success === "function" ? options.success(r) : options.success);
      for (const key of options.invalidate ?? []) qc.invalidateQueries({ queryKey: key });
      options.onSuccess?.(r);
    },
    onError: (e) => toast("error", options.errorTitle ?? "Action failed", errorMessage(e)),
  });
}
