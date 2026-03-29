"""
Style profile extractor — deterministic text-analysis model.

Uses scipy and numpy for statistical computation. No LLM required.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import stats as sp_stats  # noqa: F401

from app.analytics.registry import (
    ModelCategory,
    ModelKind,
    ModelMetadata,
)

# -------------------------------------------------------------------
# Reference word lists for formality scoring
# -------------------------------------------------------------------

FORMAL_MARKERS = frozenset(
    {
        "regarding",
        "furthermore",
        "consequently",
        "therefore",
        "accordingly",
        "pursuant",
        "hereby",
        "enclosed",
        "attached",
        "kindly",
        "respectfully",
        "sincerely",
        "appreciate",
        "acknowledge",
        "advise",
        "inform",
        "per",
        "please",
        "herein",
        "aforementioned",
        "facilitate",
    }
)

CASUAL_MARKERS = frozenset(
    {
        "hey",
        "hi",
        "thanks",
        "cool",
        "awesome",
        "great",
        "gonna",
        "wanna",
        "fyi",
        "btw",
        "asap",
        "np",
        "yeah",
        "yep",
        "nope",
        "sure",
        "quick",
        "heads-up",
        "touch base",
        "loop in",
        "ping",
    }
)

# Common greeting patterns (regex)
GREETING_PATTERNS = [
    (r"^hi\s+\w+", "Hi [Name]"),
    (r"^hey\s+\w+", "Hey [Name]"),
    (r"^hello\s+\w+", "Hello [Name]"),
    (r"^dear\s+\w+", "Dear [Name]"),
    (
        r"^good\s+(morning|afternoon|evening)",
        "Good [time of day]",
    ),
    (r"^greetings", "Greetings"),
    (
        r"^hope\s+this\s+(finds|email)",
        "Hope this finds you well",
    ),
]

# Common sign-off patterns (applied to last 3 lines)
SIGNOFF_PATTERNS = [
    (r"best\s*regards?", "Best regards"),
    (r"kind\s*regards?", "Kind regards"),
    (r"warm\s*regards?", "Warm regards"),
    (r"sincerely", "Sincerely"),
    (r"thank(s|\s+you)", "Thanks / Thank you"),
    (r"all\s+the\s+best", "All the best"),
    (r"cheers", "Cheers"),
    (r"talk\s+soon", "Talk soon"),
    (r"best,?\s*$", "Best"),
    (r"regards,?\s*$", "Regards"),
]


@dataclass
class StyleProfile:
    """Structured style profile for one advisor."""

    advisor_id: str
    email_count: int
    formality_score: float
    formality_label: str
    greeting_distribution: dict[str, float]
    signoff_distribution: dict[str, float]
    avg_word_count: float
    median_word_count: float
    stddev_word_count: float
    avg_sentence_length: float
    flesch_kincaid_grade: float
    top_vocabulary: list[tuple[str, float]]
    sample_greetings: list[str]
    sample_signoffs: list[str]


class StyleProfileExtractor:
    """
    Deterministic/heuristic model: analyze sent emails to
    build a structured style profile. No LLM dependency.
    """

    metadata = ModelMetadata(
        name="style_profile_extractor",
        version="1.0.0",
        owner="personalization",
        category=ModelCategory.PERSONALIZATION,
        kind=ModelKind.HEURISTIC,
        description=(
            "Analyze advisor's sent emails to extract "
            "formality level, greeting patterns, sign-off "
            "style, length stats, and vocabulary "
            "preferences."
        ),
        use_case=(
            "Power email drafting in the advisor's "
            "authentic writing style."
        ),
        input_freshness_seconds=604_800,
        known_limitations=(
            "Requires at least 20 emails for stable "
            "statistics.",
            "Formality scoring uses word-list heuristics, "
            "not contextual understanding.",
            "Does not distinguish between email types "
            "(client vs internal).",
        ),
    )

    def __init__(
        self,
        min_emails: int = 20,
        top_vocab_count: int = 30,
    ) -> None:
        self._min_emails = min_emails
        self._top_vocab = top_vocab_count

    def score(
        self, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """
        inputs:
            advisor_id: str
            emails: list[dict]
                Each has: body (str), subject (str),
                          sent_at (str)
        """
        advisor_id = inputs["advisor_id"]
        emails = inputs["emails"]

        if len(emails) < self._min_emails:
            return {
                "advisor_id": advisor_id,
                "status": "insufficient_data",
                "email_count": len(emails),
                "minimum_required": self._min_emails,
            }

        bodies = [e["body"] for e in emails]

        # --- Word counts ---
        word_counts = np.array(
            [len(self._tokenize(b)) for b in bodies]
        )
        avg_wc = float(np.mean(word_counts))
        median_wc = float(np.median(word_counts))
        std_wc = float(np.std(word_counts))

        # --- Formality ---
        formality = self._compute_formality(bodies)
        if formality >= 0.65:
            formality_label = "formal"
        elif formality >= 0.35:
            formality_label = "semi-formal"
        else:
            formality_label = "casual"

        # --- Greetings ---
        greeting_counts, greeting_samples = (
            self._extract_greetings(bodies)
        )
        greeting_total = (
            sum(greeting_counts.values()) or 1
        )
        greeting_dist = {
            k: round(v / greeting_total, 3)
            for k, v in greeting_counts.most_common(5)
        }

        # --- Sign-offs ---
        signoff_counts, signoff_samples = (
            self._extract_signoffs(bodies)
        )
        signoff_total = (
            sum(signoff_counts.values()) or 1
        )
        signoff_dist = {
            k: round(v / signoff_total, 3)
            for k, v in signoff_counts.most_common(5)
        }

        # --- Sentence statistics ---
        all_sentences: list[str] = []
        for body in bodies:
            sents = self._split_sentences(body)
            all_sentences.extend(sents)

        sent_lengths = (
            np.array(
                [
                    len(self._tokenize(s))
                    for s in all_sentences
                ]
            )
            if all_sentences
            else np.array([0])
        )
        avg_sent_len = float(np.mean(sent_lengths))

        # --- Flesch-Kincaid grade level ---
        fk_grade = self._flesch_kincaid_grade(bodies)

        # --- TF-IDF vocabulary ---
        top_vocab = self._compute_tfidf(bodies)

        profile = StyleProfile(
            advisor_id=advisor_id,
            email_count=len(emails),
            formality_score=round(formality, 3),
            formality_label=formality_label,
            greeting_distribution=greeting_dist,
            signoff_distribution=signoff_dist,
            avg_word_count=round(avg_wc, 1),
            median_word_count=round(median_wc, 1),
            stddev_word_count=round(std_wc, 1),
            avg_sentence_length=round(avg_sent_len, 1),
            flesch_kincaid_grade=round(fk_grade, 1),
            top_vocabulary=top_vocab[: self._top_vocab],
            sample_greetings=greeting_samples[:5],
            sample_signoffs=signoff_samples[:5],
        )

        return self._profile_to_dict(profile)

    # ---------------------------------------------------------------
    # Text analysis helpers
    # ---------------------------------------------------------------

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Simple whitespace + punctuation tokenizer."""
        return re.findall(r"[a-z']+", text.lower())

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split text into sentences."""
        return [
            s.strip()
            for s in re.split(r"[.!?]+\s+", text)
            if len(s.strip()) > 3
        ]

    def _compute_formality(
        self, bodies: list[str]
    ) -> float:
        """Ratio-based formality score (0=casual, 1=formal)."""
        formal_count = 0
        casual_count = 0
        for body in bodies:
            tokens = set(self._tokenize(body))
            formal_count += len(tokens & FORMAL_MARKERS)
            casual_count += len(tokens & CASUAL_MARKERS)
        total = formal_count + casual_count
        if total == 0:
            return 0.5  # neutral
        return formal_count / total

    @staticmethod
    def _extract_greetings(
        bodies: list[str],
    ) -> tuple[Counter, list[str]]:
        counts: Counter = Counter()
        samples: list[str] = []
        for body in bodies:
            first_line = (
                body.strip()
                .split("\n")[0]
                .strip()
                .rstrip(",")
            )
            lower = first_line.lower()
            for pattern, label in GREETING_PATTERNS:
                if re.match(pattern, lower):
                    counts[label] += 1
                    if len(samples) < 10:
                        samples.append(first_line)
                    break
            else:
                counts["(other)"] += 1
        return counts, samples

    @staticmethod
    def _extract_signoffs(
        bodies: list[str],
    ) -> tuple[Counter, list[str]]:
        counts: Counter = Counter()
        samples: list[str] = []
        for body in bodies:
            lines = [
                line.strip()
                for line in body.strip().split("\n")
                if line.strip()
            ]
            last_lines = (
                " ".join(lines[-3:]).lower()
                if lines
                else ""
            )
            for pattern, label in SIGNOFF_PATTERNS:
                if re.search(pattern, last_lines):
                    counts[label] += 1
                    if len(samples) < 10 and lines:
                        samples.append(
                            lines[-2]
                            if len(lines) >= 2
                            else lines[-1]
                        )
                    break
            else:
                counts["(other)"] += 1
        return counts, samples

    def _flesch_kincaid_grade(
        self, bodies: list[str]
    ) -> float:
        """Compute Flesch-Kincaid Grade Level."""
        total_words = 0
        total_sentences = 0
        total_syllables = 0
        for body in bodies:
            words = self._tokenize(body)
            sents = self._split_sentences(body)
            total_words += len(words)
            total_sentences += max(len(sents), 1)
            total_syllables += sum(
                self._count_syllables(w)
                for w in words
            )

        if total_words == 0 or total_sentences == 0:
            return 0.0

        asl = total_words / total_sentences
        asw = total_syllables / total_words
        return 0.39 * asl + 11.8 * asw - 15.59

    @staticmethod
    def _count_syllables(word: str) -> int:
        """Rough syllable count heuristic."""
        word = word.lower().rstrip("e")
        vowels = re.findall(r"[aeiouy]+", word)
        return max(len(vowels), 1)

    def _compute_tfidf(
        self, bodies: list[str]
    ) -> list[tuple[str, float]]:
        """Simple TF-IDF over the email corpus."""
        n_docs = len(bodies)
        df: Counter = Counter()
        tf_total: Counter = Counter()
        stop_words = frozenset(
            {
                "the", "a", "an", "is", "was", "are",
                "were", "be", "been", "being", "have",
                "has", "had", "do", "does", "did",
                "will", "would", "could", "should",
                "may", "might", "shall", "can", "to",
                "of", "in", "for", "on", "with", "at",
                "by", "from", "as", "into", "through",
                "during", "before", "after", "and",
                "but", "or", "nor", "not", "so", "yet",
                "both", "either", "neither", "each",
                "every", "all", "any", "few", "more",
                "most", "other", "some", "such", "no",
                "only", "own", "same", "than", "too",
                "very", "just", "about", "above",
                "below", "between", "up", "down", "out",
                "off", "over", "under", "again",
                "further", "then", "once", "here",
                "there", "when", "where", "why", "how",
                "what", "which", "who", "whom", "this",
                "that", "these", "those", "i", "me",
                "my", "myself", "we", "our", "ours",
                "you", "your", "yours", "he", "him",
                "his", "she", "her", "hers", "it",
                "its", "they", "them", "their", "if",
                "also", "get", "got",
            }
        )

        for body in bodies:
            tokens = self._tokenize(body)
            filtered = [
                t
                for t in tokens
                if t not in stop_words and len(t) > 2
            ]
            tf_total.update(filtered)
            unique = set(filtered)
            df.update(unique)

        # TF-IDF
        tfidf_scores: list[tuple[str, float]] = []
        for word, freq in tf_total.items():
            idf = math.log(n_docs / (1 + df[word]))
            tfidf_scores.append(
                (word, round(freq * idf, 4))
            )

        tfidf_scores.sort(
            key=lambda x: x[1], reverse=True
        )
        return tfidf_scores

    @staticmethod
    def _profile_to_dict(
        p: StyleProfile,
    ) -> dict[str, Any]:
        return {
            "advisor_id": p.advisor_id,
            "status": "complete",
            "email_count": p.email_count,
            "formality": {
                "score": p.formality_score,
                "label": p.formality_label,
            },
            "greetings": {
                "distribution": (
                    p.greeting_distribution
                ),
                "samples": p.sample_greetings,
            },
            "signoffs": {
                "distribution": (
                    p.signoff_distribution
                ),
                "samples": p.sample_signoffs,
            },
            "length": {
                "avg_words": p.avg_word_count,
                "median_words": p.median_word_count,
                "stddev_words": p.stddev_word_count,
            },
            "complexity": {
                "avg_sentence_length": (
                    p.avg_sentence_length
                ),
                "flesch_kincaid_grade": (
                    p.flesch_kincaid_grade
                ),
            },
            "vocabulary": {
                "top_terms": [
                    {"term": t, "score": s}
                    for t, s in p.top_vocabulary
                ],
            },
        }
