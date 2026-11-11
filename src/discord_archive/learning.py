"""Free, serializable character TF-IDF centroid learning, with held-out safety gates."""
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
import hashlib
import json
import math
from pathlib import Path
import re
from .models import Prediction


@dataclass
class ModelBundle:
    version: str
    idf: dict
    centroids: dict
    validation: dict
    schema: int = 1


def _features(text):
    text = re.sub(r'\s+', ' ', text.casefold())[:100_000]
    return Counter(text[i:i+n] for n in (2, 3, 4) for i in range(max(0, len(text)-n+1)))


def _norm(values):
    length = math.sqrt(sum(v*v for v in values.values()))
    return {k: v/length for k, v in values.items()} if length else {}


def _fit(examples, version):
    vectors = [(_features(text), label) for text, label in examples]
    df = Counter()
    for vec, _ in vectors:
        df.update(vec.keys())
    # Bound model vocabulary on personal archives; rare features omitted only past capacity.
    idf = {f: math.log((1+len(vectors))/(1+count)) + 1 for f, count in df.most_common(50_000)}
    sums = defaultdict(Counter)
    for vec, label in vectors:
        sums[label].update(_norm({f: (1+math.log(c))*idf[f] for f, c in vec.items() if f in idf}))
    return ModelBundle(version, idf, {k: _norm(v) for k, v in sums.items()}, {})


def train(examples: list[tuple[str, str]], version: str) -> ModelBundle:
    # Identical content must not leak across training and validation or inflate sample count.
    grouped = defaultdict(set)
    for text, label in examples:
        if text.strip():
            grouped[re.sub(r'\s+', ' ', text.casefold()).strip()].add(label)
    unique = [(text, next(iter(labels))) for text, labels in sorted(grouped.items()) if len(labels) == 1]
    model = _fit(unique, version)
    train_set, held = [], []
    per_class = defaultdict(list)
    for sample in unique:
        per_class[sample[1]].append(sample)
    for samples in per_class.values():
        for i, sample in enumerate(samples):
            (held if i % 5 == 0 else train_set).append(sample)
    evaluator = _fit(train_set, version)
    counts = defaultdict(lambda: {'predicted': 0, 'correct': 0, 'actual': 0})
    for text, actual in held:
        guess = predict(text, {}, evaluator).channel_id
        counts[actual]['actual'] += 1
        if guess:
            counts[guess]['predicted'] += 1
            counts[guess]['correct'] += int(guess == actual)
    model.validation = dict(counts)
    return model


def predict(text: str, metadata: dict, model: ModelBundle | None) -> Prediction:
    tags = [t for t in metadata.get('tags', []) if re.search(r'(?<!\w)' + re.escape(t) + r'(?!\w)', text, re.I)]
    if not model or not model.centroids or not text.strip():
        return Prediction(tags=tags)
    vector = _norm({f: (1+math.log(c))*model.idf[f] for f, c in _features(text).items() if f in model.idf})
    scores = sorted(((sum(v*center.get(f, 0) for f, v in vector.items()), label) for label, center in model.centroids.items()), reverse=True)
    score, label = scores[0]
    stats = model.validation.get(label, {})
    precise = stats.get('predicted', 0) >= 20 and stats.get('actual', 0) >= 20 and stats.get('correct', 0) / max(1, stats.get('predicted', 0)) >= .95
    clear = len(scores) > 1 and score >= .90 and score - scores[1][0] >= .15
    return Prediction(label, score, tags, model.version, not (precise and clear))


def save_model(model, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(asdict(model), ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def load_model(path: Path):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('schema') != 1:
            return None
        model = ModelBundle(**data)
        if not isinstance(model.centroids, dict) or not isinstance(model.idf, dict):
            return None
        return model
    except (OSError, ValueError, TypeError):
        return None
