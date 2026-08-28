import math
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

HANDBOOK_DIR = Path(__file__).parent / "handbooks" / "void-docs"
HANDBOOK_REPO = "https://github.com/void-linux/void-docs.git"

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "been", "being", "this", "that", "these",
    "those", "it", "its", "as", "at", "by", "from", "do", "does", "did", "have",
    "has", "had", "not", "no", "can", "will", "would", "should", "could", "how",
    "what", "which", "who", "when", "where", "why", "your", "you", "we", "they",
    "use", "using", "used", "the", "there", "their", "than", "then", "also",
}

TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    tokens = TOKEN_RE.findall(text.lower())
    return [t for t in tokens if t not in STOPWORDS and len(t) > 1]


def ensure_handbook() -> bool:
    """Clone void-docs jika belum ada, update jika sudah ada."""
    try:
        if not HANDBOOK_DIR.exists():
            HANDBOOK_DIR.parent.mkdir(parents=True, exist_ok=True)
            print("Mengunduh void-docs handbook...", file=sys.stderr)
            subprocess.run(
                ["git", "clone", "--depth", "1", HANDBOOK_REPO, str(HANDBOOK_DIR)],
                check=True,
                capture_output=True,
            )
        else:
            subprocess.run(
                ["git", "-C", str(HANDBOOK_DIR), "pull", "--quiet"],
                check=True,
                capture_output=True,
            )
        return True
    except Exception as e:
        print(f"Gagal mengunduh void-docs: {e}", file=sys.stderr)
        return False


def load_chunks() -> list[dict]:
    """Baca semua file markdown, pecah per judul menjadi chunk."""
    chunks = []
    if not HANDBOOK_DIR.exists():
        return chunks
    for md in HANDBOOK_DIR.rglob("*.md"):
        rel = md.relative_to(HANDBOOK_DIR)
        try:
            content = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        parts = re.split(r"^(#{1,3})\s+(.+)$", content, flags=re.MULTILINE)
        if len(parts) < 3:
            text = content.strip()
            if text:
                chunks.append({"source": str(rel), "title": rel.name, "text": text[:8000]})
            continue
        title = None
        i = 1
        while i < len(parts):
            if i + 1 < len(parts):
                title = parts[i + 1].strip()
                i += 3
            else:
                i += 1
        # Dapatkan teks per bagian heading
        lines = content.splitlines()
        current_title = None
        current = []
        for line in lines:
            m = re.match(r"^(#{1,3})\s+(.+)$", line)
            if m:
                if current:
                    chunks.append({
                        "source": str(rel),
                        "title": current_title or rel.name,
                        "text": "\n".join(current).strip()[:8000],
                    })
                current_title = m.group(2).strip()
                current = []
            else:
                current.append(line)
        if current:
            chunks.append({
                "source": str(rel),
                "title": current_title or rel.name,
                "text": "\n".join(current).strip()[:8000],
            })
    return [c for c in chunks if len(c["text"]) > 50]


class HandbookIndex:
    def __init__(self, chunks: list[dict]):
        self.chunks = chunks
        self.idf: dict[str, float] = {}
        self.vectors: list[dict[str, float]] = []
        self._build()

    def _build(self) -> None:
        df: Counter[str] = Counter()
        token_lists: list[list[str]] = []
        for c in self.chunks:
            tokens = tokenize(c["text"])
            c["_tokens"] = tokens
            token_lists.append(tokens)
            df.update(set(tokens))
        n = max(len(self.chunks), 1)
        self.idf = {t: math.log(1 + n / (1 + freq)) for t, freq in df.items()}
        for tokens in token_lists:
            tf = Counter(tokens)
            vec = {t: (1 + math.log(tf[t])) * self.idf[t] for t in tf}
            norm = math.sqrt(sum(v * v for v in vec.values()))
            self.vectors.append({t: v / norm for t, v in vec.items()} if norm else {})

    def _query_vector(self, query: str) -> dict[str, float]:
        q_tokens = tokenize(query)
        q_tf = Counter(q_tokens)
        vec = {t: (1 + math.log(q_tf[t])) * self.idf.get(t, 0) for t in q_tf}
        norm = math.sqrt(sum(v * v for v in vec.values()))
        return {t: v / norm for t, v in vec.items()} if norm else {}

    def search(self, query: str, top_k: int = 4, min_score: float = 0.0) -> list[dict]:
        if not self.chunks:
            return []
        q_vec = self._query_vector(query)
        if not q_vec:
            return []
        scored = []
        for i, vec in enumerate(self.vectors):
            score = sum(q_vec[t] * vec.get(t, 0) for t in q_vec)
            if score >= min_score:
                scored.append((score, i))
        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, i in scored[:top_k]:
            chunk = dict(self.chunks[i])
            chunk.pop("_tokens", None)
            chunk["score"] = round(score, 4)
            results.append(chunk)
        return results

    @staticmethod
    def format_context(results: list[dict]) -> str:
        parts = []
        for r in results:
            parts.append(
                f"[Sumber: {r['source']} | Judul: {r['title']}]\n{r['text']}"
            )
        return "\n\n---\n\n".join(parts)


if __name__ == "__main__":
    if not ensure_handbook():
        sys.exit(1)
    chunks = load_chunks()
    print(f"Index {len(chunks)} chunk dari handbook.")
    index = HandbookIndex(chunks)
    query = sys.argv[1] if len(sys.argv) > 1 else "install package xbps"
    for r in index.search(query):
        print(f"\n[{r['score']}] {r['source']} :: {r['title']}")