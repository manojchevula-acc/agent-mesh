import { apiClient } from "@/lib/apiClient";
import type { MeshResult, MeshUser, NodeHealth } from "@/types/mesh";

export async function queryMesh(username: string, query: string): Promise<MeshResult> {
  const { data } = await apiClient.post<MeshResult>("/api/query", { username, query });
  return data;
}

export async function listUsers(): Promise<MeshUser[]> {
  const { data } = await apiClient.get<MeshUser[]>("/api/users");
  return data;
}

export async function loginUser(username: string): Promise<MeshUser> {
  const { data } = await apiClient.post<MeshUser>("/api/login", { username });
  return data;
}

export async function getMeshStatus(): Promise<NodeHealth[]> {
  const { data } = await apiClient.get<NodeHealth[]>("/api/mesh/status");
  return data;
}
