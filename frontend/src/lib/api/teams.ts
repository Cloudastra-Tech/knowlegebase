// (being written)
import { request } from "@/lib/api/client"

export const teamsApi = {
  get: () => request<unknown>("/admin/teams"),
}
