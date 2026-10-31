export interface BriefingItem {
  label: string
  detail: string
  urgency: 'high' | 'medium' | 'low'
  actionUrl: string | null
  metadata: Record<string, unknown>
}

export interface BriefingSection {
  kind: string
  title: string
  items: BriefingItem[]
  summary: string
}

export interface BriefingPayload {
  generatedAt: string
  projectId: number | null
  sections: BriefingSection[]
}
