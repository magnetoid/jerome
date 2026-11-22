import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { apiFetch } from "@/lib/api";

export type Me = {
  id: number | null;
  email: string | null;
  name: string;
  must_change_password: boolean;
};

export const ME_KEY = ["me"] as const;

export function useMe() {
  return useQuery({ queryKey: ME_KEY, queryFn: () => apiFetch<Me>("/v1/auth/me") });
}

/** Ends the session, drops everything cached for this user and goes to /login. */
export function useLogout() {
  const client = useQueryClient();
  const navigate = useNavigate();
  return async () => {
    try {
      await apiFetch<void>("/v1/auth/logout", { method: "POST" });
    } finally {
      client.clear();
      navigate("/login");
    }
  };
}
