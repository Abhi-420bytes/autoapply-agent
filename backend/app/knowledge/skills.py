"""Deterministic skill tagging (no LLM): a term only becomes a tag if it literally
appears in the text, so tags can't be invented."""

from __future__ import annotations

import re
from collections.abc import Iterable

# Common technical terms. The user's own profile skills, GitHub languages/topics and
# template SKILLS section are added on top at runtime.
BASE_VOCAB: tuple[str, ...] = (
    "Python",
    "Java",
    "JavaScript",
    "TypeScript",
    "C",
    "C++",
    "C#",
    "Go",
    "Rust",
    "Kotlin",
    "Swift",
    "Ruby",
    "PHP",
    "Scala",
    "R",
    "MATLAB",
    "SQL",
    "Bash",
    "Dart",
    "Solidity",
    "HTML",
    "CSS",
    "React",
    "Next.js",
    "Vue",
    "Angular",
    "Svelte",
    "Node.js",
    "Express",
    "Django",
    "Flask",
    "FastAPI",
    "Spring",
    "Spring Boot",
    "Rails",
    "Laravel",
    ".NET",
    "Flutter",
    "React Native",
    "Android",
    "iOS",
    "Tailwind",
    "Redux",
    "GraphQL",
    "REST",
    "gRPC",
    "WebSocket",
    "PostgreSQL",
    "MySQL",
    "SQLite",
    "MongoDB",
    "Redis",
    "Cassandra",
    "DynamoDB",
    "Elasticsearch",
    "Kafka",
    "RabbitMQ",
    "Celery",
    "Docker",
    "Kubernetes",
    "Terraform",
    "Ansible",
    "Jenkins",
    "GitHub Actions",
    "CI/CD",
    "AWS",
    "GCP",
    "Azure",
    "Linux",
    "Git",
    "Nginx",
    "Firebase",
    "Supabase",
    "Vercel",
    "Heroku",
    "Machine Learning",
    "Deep Learning",
    "NLP",
    "Computer Vision",
    "PyTorch",
    "TensorFlow",
    "Keras",
    "scikit-learn",
    "Pandas",
    "NumPy",
    "OpenCV",
    "Hugging Face",
    "LangChain",
    "LangGraph",
    "LLM",
    "RAG",
    "Transformers",
    "Spark",
    "Hadoop",
    "Airflow",
    "dbt",
    "Tableau",
    "Power BI",
    "Excel",
    "Selenium",
    "Playwright",
    "Jest",
    "pytest",
    "JUnit",
    "Microservices",
    "System Design",
    "Data Structures",
    "Algorithms",
    "OOP",
    "Agile",
    "LaTeX",
    "Figma",
    "Unity",
    "Blockchain",
    "Web3",
    "OAuth",
    "JWT",
    "SQLAlchemy",
    "Pydantic",
    "Prisma",
    "Streamlit",
    "Gradio",
    "OpenAI",
    "Arduino",
    "Raspberry Pi",
)

_ALIASES = {
    "golang": "Go",
    "postgres": "PostgreSQL",
    "k8s": "Kubernetes",
    "js": "JavaScript",
    "ts": "TypeScript",
    "reactjs": "React",
    "nextjs": "Next.js",
    "nodejs": "Node.js",
    "sklearn": "scikit-learn",
    "tf": "TensorFlow",
    "ml": "Machine Learning",
    "dl": "Deep Learning",
    "gcp": "GCP",
    "amazon web services": "AWS",
}
# Single letters / ambiguous words need exact-case matches.
_CASE_SENSITIVE = {"C", "R", "Go", "Spring", "Excel", "Unity", "Rails", "Express"}


def _pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term)
    flags = 0 if term in _CASE_SENSITIVE else re.IGNORECASE
    # word-ish boundaries that also work for C++, C#, .NET, Node.js
    return re.compile(rf"(?<![A-Za-z0-9+#.]){escaped}(?![A-Za-z0-9+#])", flags)


class SkillTagger:
    def __init__(self, extra_terms: Iterable[str] = ()) -> None:
        terms: dict[str, str] = {}
        for t in (*BASE_VOCAB, *extra_terms):
            t = t.strip()
            if 1 <= len(t) <= 40:
                terms.setdefault(t.lower(), t)
        self._patterns = [(canon, _pattern(canon)) for canon in terms.values()]
        self._aliases = [(canon, _pattern(alias)) for alias, canon in _ALIASES.items()]

    def tags(self, text: str) -> list[str]:
        found = {canon for canon, p in self._patterns if p.search(text)}
        found |= {canon for canon, p in self._aliases if p.search(text)}
        # "C" inside "C++"/"C#" contexts is handled by boundaries; drop "C" if only "C++".
        return sorted(found, key=str.lower)


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9%]+", " ", text.lower()).strip()
