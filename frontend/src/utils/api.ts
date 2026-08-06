import axios from "axios";
import type { ApiResponse, ApiError } from "@/types";

const apiClient = axios.create({
  baseURL: "/api/v1",
  timeout: 30000,
  headers: {
    "Content-Type": "application/json",
  },
});

apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Backend 4xx/5xx error envelope: response.data.detail.error (AppException structure)
export interface ApiErrorDetail {
  code?: string;
  message?: string;
  description?: string;
}

// Extract a readable error message from an error object for callers to display (falls back to the given fallback)
export function getApiErrorMessage(err: unknown, fallback: string): string {
  if (axios.isAxiosError(err)) {
    const data = err.response?.data as
      | { detail?: { error?: ApiErrorDetail } | string }
      | undefined;
    const detail = data?.detail;
    if (typeof detail === "string" && detail) return detail;
    if (detail?.error?.message) return detail.error.message;
    if (detail?.error?.description) return detail.error.description;
  }
  return fallback;
}

// Extract the backend error code (e.g. INVALID_CREDENTIALS) from an error object, if present
export function getApiErrorCode(err: unknown): string | undefined {
  if (axios.isAxiosError(err)) {
    const data = err.response?.data as
      | { detail?: { error?: ApiErrorDetail } }
      | undefined;
    return data?.detail?.error?.code;
  }
  return undefined;
}

// Drop the local session and bounce the user to the login page of the entry that created it
// (admin sessions go back to /ibadmin, SSO sessions to /login)
function clearSessionAndRedirectToLogin() {
  const entry = localStorage.getItem("session_entry");
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  localStorage.removeItem("session_entry");
  window.location.href = entry === "admin" ? "/ibadmin" : "/login";
}

apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;

    // 401s on the login flows (SSO: POST /auth/sso/{provider}, local admin: POST /auth/admin/login)
    // are rethrown to the caller for display, skipping token refresh / clearing auth state / redirects
    // so login failures are not bounced back to the login page with no visible error
    const requestUrl: string = originalRequest?.url || "";
    if (
      requestUrl.includes("/auth/sso/") ||
      requestUrl.includes("/auth/admin/login")
    ) {
      return Promise.reject(error);
    }

    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true;

      const refreshToken = localStorage.getItem("refresh_token");
      if (refreshToken) {
        try {
          const res = await axios.post("/api/v1/auth/refresh", {
            refresh_token: refreshToken,
          });
          const data = res.data.data;
          localStorage.setItem("access_token", data.access_token);

          originalRequest.headers.Authorization = `Bearer ${data.access_token}`;
          return apiClient(originalRequest);
        } catch {
          clearSessionAndRedirectToLogin();
          return Promise.reject(error);
        }
      } else {
        clearSessionAndRedirectToLogin();
        return Promise.reject(error);
      }
    }

    return Promise.reject(error);
  },
);

export async function apiGet<T>(
  url: string,
  params?: Record<string, unknown>,
): Promise<ApiResponse<T>> {
  const response = await apiClient.get<ApiResponse<T>>(url, { params });
  return response.data;
}

export async function apiPost<T>(
  url: string,
  data?: Record<string, unknown>,
): Promise<ApiResponse<T>> {
  const response = await apiClient.post<ApiResponse<T>>(url, data);
  return response.data;
}

export async function apiPut<T>(
  url: string,
  data?: Record<string, unknown>,
): Promise<ApiResponse<T>> {
  const response = await apiClient.put<ApiResponse<T>>(url, data);
  return response.data;
}

export async function apiDelete<T>(url: string): Promise<ApiResponse<T>> {
  const response = await apiClient.delete<ApiResponse<T>>(url);
  return response.data;
}

export function isApiError(response: unknown): response is ApiError {
  return (
    typeof response === "object" &&
    response !== null &&
    "success" in response &&
    (response as ApiError).success === false &&
    "error" in response
  );
}

export { apiClient };
