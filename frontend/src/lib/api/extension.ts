// (being written)
import { request } from "@/lib/api/client"

export const extensionApi = {
  get: () => request<unknown>("/extension"),
}
