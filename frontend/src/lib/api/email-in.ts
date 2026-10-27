// (being written)
import { request } from "@/lib/api/client"

export const emailInApi = {
  get: () => request<unknown>("/admin/email-in"),
}
