# Review of external code for AdvFlow

Date reviewed: 2026-10-21

## Conclusion

The two archives contain useful implementation patterns, but neither repository should be installed wholesale inside the Streamlit app.

- **Gemini CLI** is a TypeScript/Node terminal agent. Its strongest transferable ideas are reusable skill instructions, structured context, tools/MCP boundaries, and safe web-fetch patterns. Its full runtime is not a drop-in component for this Python/Streamlit deployment.
- **Hermes Agent** is a broader Python agent platform with CLI, gateway, terminal/browser backends, tool providers, plugins, memory, and image generation. The whole application would add a large dependency and security surface that the AdvFlow use case does not need.
- The article-illustration skill bundled in Hermes explicitly uses a **Type × Style × Palette** method and a staged workflow. AdvFlow now expresses these decisions in its image studio instead of asking for a generic legal image.
- Hermes' OpenRouter image-generation provider demonstrates a provider/catalog architecture, parameter-capability checks, reference-image input, error reporting, and cost tracking. AdvFlow now implements a smaller native integration for OpenRouter's unified Image API, using the existing OpenRouter key.

## Changes made to AdvFlow

### 1. Creative direction
The studio adds a composition type, rendering style, visual density, area, audience, brand palette, and per-slide direction. Its prompt specifies a concept tied to the slide, placement of negative space for typography, and avoidance of generic legal stock-image clichés. This adapts ideas from the Hermes bundled baoyu-article-illustrator skill:
- Archive path: `hermes-agent-main/optional-skills/creative/baoyu-article-illustrator/SKILL.md`
- Upstream noted by the skill: https://github.com/JimLiu/baoyu-skills
- The bundled skill declares MIT; its method is referenced, not copied wholesale into AdvFlow.

### 2. Direct image generation
The image studio can request a live model catalog and generate an image with the OpenRouter Image API:
- Catalog: `GET https://openrouter.ai/api/v1/images/models`
- Generation: `POST https://openrouter.ai/api/v1/images`
- Output bytes: `data[0].b64_json`
- Optional reference images: `input_references`, only when the selected model declares support
- Cost: display `usage.cost` when returned by the provider

The feature requires `OPENROUTER_API_KEY`, an image-capable model and available credit. It does not use `openrouter/free` for image calls; image requests can incur separate charges. The user must confirm the image charge in the UI before the call.

Canonical docs: https://openrouter.ai/blog/tutorials/image-generation/ and https://openrouter.ai/collections/image-models/.

### 3. Keep the Gemini Plus workflow
Manual generation using the user's Gemini Plus subscription remains available: AdvFlow creates a per-slide prompt, the user generates the image in the Gemini application, then uploads it for typography and brand treatment. This path does not require an API key.

### 4. Do not reuse the Gemini CLI OAuth session in the web app
Gemini CLI is Apache-2.0 licensed, but software licensing does not override Google's service terms. The official Gemini CLI terms state that directly accessing services behind its sign-in/OAuth from third-party software is prohibited and can result in account suspension: https://geminicli.com/docs/resources/tos-privacy/. AdvFlow therefore does not read, copy, or reuse Gemini CLI OAuth tokens. Any automated Google image call must use an official API credential and a model/plan with the required access.

### 5. Why the full repositories were not imported
Gemini CLI's Node runtime and Hermes' multi-provider agent, terminal, browser, and gateway infrastructure would add complexity that is not needed for this task. The current integration is a focused Streamlit/Python implementation around existing dependencies (`requests`, Pillow) and the OpenRouter API, keeping secrets in Streamlit Secrets and leaving the manual Gemini Plus path untouched.

## Licenses and attribution

- Gemini CLI: Apache License 2.0 — https://github.com/google-gemini/gemini-cli/blob/main/LICENSE
- Hermes Agent: MIT License — https://github.com/NousResearch/hermes-agent/blob/main/LICENSE
- Article illustrator skill: declares MIT in its `SKILL.md`; its noted upstream is JimLiu/baoyu-skills.

This review records the code and ideas evaluated, what was adapted, and why. AdvFlow's new functions were implemented for the existing app rather than importing the two repositories as dependencies.