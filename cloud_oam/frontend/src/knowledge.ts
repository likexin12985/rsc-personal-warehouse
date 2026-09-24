export type KnowledgeItem = {
  code: string;
  name: string;
  category: string;
  model: string;
  note: string;
  sourceSheet: string;
  sourceRow: number;
};

export type KnowledgeCatalog = {
  schemaVersion: number;
  status: string;
  sourceUrl: string;
  verifiedAt: string | null;
  sourceSha256: string | null;
  items: KnowledgeItem[];
};

export function searchKnowledge(items: KnowledgeItem[], query: string, category: string) {
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  return items.filter((item) => {
    if (category && item.category !== category) return false;
    const text = [item.code, item.name, item.category, item.model, item.note, item.sourceSheet].join(" ").toLocaleLowerCase();
    return words.every((word) => text.includes(word));
  });
}
