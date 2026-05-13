import difflib
import re

STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "when",
    "where", "what", "how", "are", "was", "were", "been", "have", "has",
    "had", "will", "would", "could", "should", "may", "might", "must",
    "can", "not", "but", "than", "then", "them", "they", "their", "there",
    "here", "about", "over", "under", "again", "once", "more", "most",
    "some", "such", "only", "own", "same", "so", "than", "too", "very",
    "just", "now",
}


def extract_keywords(title):
    if not title:
        return set()
    words = re.findall(r"[a-zA-Z0-9]+", title.lower())
    return {w for w in words if len(w) > 3 and w not in STOPWORDS}


def match_score(bug_a, bug_b):
    if bug_a.pk == bug_b.pk:
        return 1.0, "identical"

    title_a = (bug_a.title or "").lower()
    title_b = (bug_b.title or "").lower()

    # Sequence similarity
    seq_sim = difflib.SequenceMatcher(None, title_a, title_b).ratio()

    # Keyword overlap
    kw_a = extract_keywords(title_a)
    kw_b = extract_keywords(title_b)
    if not kw_a and not kw_b:
        kw_score = 0.0
    else:
        intersection = len(kw_a & kw_b)
        union = len(kw_a | kw_b)
        kw_score = intersection / union if union else 0.0

    # Combined score: 60% sequence, 40% keyword
    score = seq_sim * 0.6 + kw_score * 0.4

    reason = f"seq={seq_sim:.2f}, kw={kw_score:.2f}"
    return round(score, 3), reason


def find_duplicates(bugs, threshold=0.75):
    results = []
    seen_pairs = set()
    bugs_list = list(bugs)

    for i, bug_a in enumerate(bugs_list):
        for bug_b in bugs_list[i + 1:]:
            pair = tuple(sorted([bug_a.pk, bug_b.pk]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            score, reason = match_score(bug_a, bug_b)
            if score >= threshold:
                results.append({
                    "bugs": [bug_a, bug_b],
                    "score": score,
                    "reason": reason,
                })

    # Sort by score descending
    results.sort(key=lambda r: r["score"], reverse=True)
    return results
