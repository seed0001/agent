# Image Generation (Grok Imagine)

Generate images from text prompts via xAI's Grok Imagine API. Use for visual content, illustrations, data visualization, style experiments. **Cost-aware**: each image consumes API quota; track usage to stay within budget.

## Tools

| Tool | Use |
|------|-----|
| `get_image_usage` | Check daily count, limit, remaining. Call **before** generate_image. |
| `generate_image` | Create image(s) from a text prompt. |

## generate_image

**Parameters:**
- `prompt` (required) – Text description of the image (e.g. "Abstract geometric art in blue and gold", "Bar chart showing Q1–Q4 revenue growth", "Mountain landscape at sunrise, watercolor style")
- `n` (optional) – Number of images (1–4). Default: 1. More = more cost.
- `aspect_ratio` (optional) – e.g. `1:1`, `16:9`, `4:3`, `3:2`. Default: `1:1`. Use `16:9` for widescreen, `9:16` for mobile/portrait.
- `save_path` (optional) – Path to save the first image locally (e.g. `outputs/art.png`)

**Flow:**
1. Call `get_image_usage` to see remaining quota
2. If remaining > 0, call `generate_image(prompt="...", ...)`
3. Every image is automatically saved to `generated_images/` (or `IMAGE_OUTPUT_DIR`). Optional `save_path` copies the first image to an additional location.

**Output:**
- All images saved to `generated_images/` (project root, gitignored). Override with `IMAGE_OUTPUT_DIR` in `.env` (e.g. `~/Pictures/Adam`).

**Budget:**
- Daily limit defaults to 20. Set `IMAGE_GEN_DAILY_LIMIT` in `.env` to change.
- Usage is stored in `data/image_usage.json` (by date, total).
- When limit is reached, the tool returns an error. Try again tomorrow.

**Use cases (from Creator request):**
- Original images, illustrations, abstract art for website and projects
- Data visualization (charts, graphical representations of system test results)
- Style experimentation (different art styles, themes, visual identity)
