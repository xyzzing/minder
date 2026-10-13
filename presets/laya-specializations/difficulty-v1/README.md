# difficulty-v1

Portable Laya specialization artifact (format version 1).

- Base model: `convaiinnovations/laya`
- Strategy: `whitened_prototypes`
- Task: Grade a coding-agent request by the reasoning it requires: mechanical (localized, explicit, obvious path), routine (normal judgement across few known files), complex (multi-file or system design, non-obvious debugging, migrations, concurrency, security, performance), expert_or_ambiguous (conflicting or missing requirements, domain expertise, high blast radius, novel architecture, or a human decision is needed).
- Decision labels: mechanical, routine, complex, expert_or_ambiguous
- License: Apache-2.0

```python
from laya_studio import SpecializedLaya
agent = SpecializedLaya.from_pretrained(".")
result = agent.predict("your input")
```

Laya model weights by Convai Innovations and upstream contributors.
