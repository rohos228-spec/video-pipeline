# Image prompt skills — модульная нарезка

Это **не** «1 промт = 1 скил». Один style-template разобран на модули:

```
orchestrate
   → fill-scene-slots      (сюжет → слоты)
   → apply-style-pack      (стиль из packs/*.md)
   → enforce-scene-rules   (1 сцена, no text, gore…)
   → compose-positive      (PROMPT)
   → compose-negative      (NEGATIVE)
   → self-check            (гейт)
```

## Как применять
1. В Agent chat: `/image-prompt-orchestrate` и опиши сцену + стиль  
   («пластилин», «вязаный», «нуар», «trash polka»).
2. Или зови модули по отдельности, если отлаживаешь один шаг.
3. Style packs лежат в  
   `image-prompt-apply-style-pack/references/packs/`.

## Packs из исходных промтов
| pack | исходник |
|------|----------|
| plasticine | пластилин.txt.md |
| knitted-2d | вязаный 2д стиль.txt.md |
| noir-bloody | грязный темный кровавый.txt.md |
| trash-polka | полька кровавая.txt.md |
