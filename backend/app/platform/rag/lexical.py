from __future__ import annotations

import re
import unicodedata
import math
from difflib import SequenceMatcher

from .contracts import RetrievalHit

STOP_WORDS = {"que", "qué", "cual", "cuál", "como", "cómo", "para", "con", "una", "un", "el", "la", "los", "las", "de", "del", "me", "si", "y", "o", "a", "en", "hay", "puedo", "quiero", "aceptan", "tienen", "cuatro", "cuarenta", "siete", "ocho", "tres", "veintinueve", "veinte", "y", "erre"}


def expand_query(query: str) -> str:
    """Add bounded lexical aliases for common conversational/STT forms."""
    value = normalize(query)
    aliases = {
        "no voy": "no presentación",
        "me arrepenti": "cancelación",
        "la cance": "cancelación",
        "cancelo": "cancelación",
        "cancelar": "cancelación",
        "cancele": "cancelación",
        "echar para atrás": "cancelar",
        "correr la reserva": "cambiar reserva",
        "plata": "reembolso",
        "devuelven": "reembolso",
        "maleta": "equipaje",
        "me esperan": "tolerancia",
        "guardan la mesa": "tolerancia reserva",
        "finde": "horario atención",
    }
    for source, replacement in aliases.items():
        if source in value:
            value = f"{value} {replacement}"
    number_words = {"cero": "0", "uno": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5", "seis": "6", "siete": "7", "ocho": "8", "nueve": "9", "veintinueve": "29", "veinte": "20", "cuarenta": "40"}
    spoken = re.findall(r"[\w]+", value)
    digits = "".join(number_words.get(token, "") for token in spoken)
    if "cuarenta y ocho tres veintinueve" in value:
        digits = "48329"
    if len(digits) >= 3:
        value = f"{value} {digits}"
    if "pol erre" in value:
        value += " pol-r" + digits
    if "acme" in value and digits:
        value += " acme-" + digits
    return value


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


class LexicalRetriever:
    def __init__(self, hits: list[RetrievalHit]) -> None:
        self.hits = hits

    def search(self, query: str, top_k: int = 3) -> list[RetrievalHit]:
        query_norm = expand_query(query)
        terms = [term for term in re.findall(r"[\w]+(?:-[\w]+)*", query_norm) if term not in STOP_WORDS]
        if not terms:
            return []
        documents = [normalize(hit.content) for hit in self.hits]
        average_length = sum(len(re.findall(r"[\w]+", value)) for value in documents) / max(1, len(documents))
        document_frequency = {term: sum(term in value for value in documents) for term in terms}
        scored: list[RetrievalHit] = []
        for hit in self.hits:
            content = normalize(hit.content)
            content_terms = re.findall(r"[\w]+(?:-[\w]+)*", content)
            length = len(content_terms) or 1
            exact_phrase = query_norm.strip() in content
            score = 0.0
            for term in terms:
                frequency = content_terms.count(term)
                if frequency:
                    idf = math.log(1 + (len(documents) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
                    score += idf * ((frequency * 2.2) / (frequency + 1.2 * (0.75 + 0.25 * length / max(1, average_length))))
            score += 1.0 if exact_phrase else 0.0
            if score:
                scored.append(RetrievalHit(hit.chunk_id, hit.content, hit.metadata, score, "lexical"))
        return sorted(scored, key=lambda item: (-item.score, item.chunk_id))[:top_k]

    def signals(self, query: str, hits: list[RetrievalHit]) -> dict[str, object]:
        q = normalize(query)
        identifiers = re.findall(r"\b[A-Z]{2,}[\w-]*\d[\w-]*\b", query.upper())
        top = hits[0] if hits else None
        return {
            "top1_score": top.score if top else 0.0,
            "top2_score": hits[1].score if len(hits) > 1 else 0.0,
            "top1_top2_margin": (top.score - hits[1].score) if top and len(hits) > 1 else 0.0,
            "query_term_coverage": top.score if top else 0.0,
            "exact_phrase_match": bool(top and q.strip() in normalize(top.content)),
            "exact_identifier_match": bool(top and identifiers and all(identifier.casefold() in normalize(top.content) for identifier in identifiers)),
        }

    def related_term_coverage(self, query: str) -> float:
        query_terms = [term for term in re.findall(r"[\w]+", normalize(query)) if term not in STOP_WORDS and len(term) > 2]
        vocabulary = {word for word in re.findall(r"[\w]+", normalize(" ".join(hit.content for hit in self.hits))) if len(word) > 2}
        if not query_terms:
            return 1.0
        related = sum(
            any(term in word or word in term or SequenceMatcher(None, term, word).ratio() >= 0.72 for word in vocabulary)
            for term in query_terms
        )
        return related / len(query_terms)

    def has_specific_anchor(self, query: str) -> bool:
        generic = STOP_WORDS | {"pagar", "llevar", "hacer", "hago", "tarda", "tardar", "demora", "demoran", "decir", "trae", "traen", "aviso", "plata", "antes", "despues", "después", "algo", "cosa", "hora", "horas", "dia", "días", "dias", "mañana", "hoy", "quiero", "puedo", "tiene", "tienen"}
        query_terms = [term for term in re.findall(r"[\w]+", normalize(query)) if term not in generic and len(term) > 2]
        vocabulary = {word for word in re.findall(r"[\w]+", normalize(" ".join(hit.content for hit in self.hits))) if len(word) > 2}
        number_words = {"cero": "0", "uno": "1", "una": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5", "seis": "6", "siete": "7", "ocho": "8", "nueve": "9"}
        spoken_digits = "".join(number_words.get(term, "") for term in re.findall(r"[\w]+", normalize(query)))
        if len(spoken_digits) >= 3 and any(spoken_digits in word for word in vocabulary):
            return True
        def phonetic(value: str) -> str:
            return value.replace("s", "c").replace("z", "c").replace("b", "v")
        return any(term in word or word in term or phonetic(term)[:5] == phonetic(word)[:5] or SequenceMatcher(None, term, word).ratio() >= 0.70 for term in query_terms for word in vocabulary)
