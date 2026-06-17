import { api } from "../api";

export interface TalentMatch {
  candidate_id: string;
  candidate_name: string | null;
  candidate_email: string | null;
  similarity: number;
  top_skills: string[];
  experience_years: number | null;
  current_title: string | null;
  current_company: string | null;
  location: string | null;
  last_application_status: string | null;
  last_role_applied: string | null;
}

export interface TalentSearchResponse {
  query: string | null;
  matches: TalentMatch[];
  total: number;
}

export const talentSearchApi = {
  query(q: string, limit = 20) {
    return api.get<TalentSearchResponse>(
      `/talent-search/query?q=${encodeURIComponent(q)}&limit=${limit}`,
    );
  },
  similar(candidateId: string, limit = 15) {
    return api.get<TalentSearchResponse>(
      `/talent-search/similar/${candidateId}?limit=${limit}`,
    );
  },
  matchRole(roleId: string, limit = 20) {
    return api.get<TalentSearchResponse>(
      `/talent-search/match-role/${roleId}?limit=${limit}&exclude_current_applicants=true`,
    );
  },
};
