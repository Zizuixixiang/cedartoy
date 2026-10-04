---
name: cedar-ui-review
description: CedarToy-specific UI implementation and review rules. Use for any visible UI/CSS/interaction change in CedarToy, especially Duel mobile UI.
---

# CedarToy UI Review

Treat the existing CedarToy/Duel interface as the design system. Do not invent a parallel visual language.

## Before editing
1. Inspect the exact current component and at least two nearby established components that serve similar roles.
2. Record the existing font family, font size, line height, border width, shadow, spacing, button height, and mobile behavior before choosing new values.
3. Prefer reusing existing classes/components. Only add new CSS when the existing component cannot express the required behavior.
4. If the user provides a screenshot/reference, use it as an interaction/layout reference, not as permission to replace CedarToy's visual identity.

## Typography and hierarchy
- Body/supporting text must not become larger than established primary content without a specific reason.
- Autocomplete, helper text, metadata, IDs, hints, and secondary labels must use a smaller, quieter type scale than primary game text.
- Do not use font: inherit on menus, popovers, autocomplete rows, helper UI, or compact controls unless the inherited size has been verified visually.
- Keep one obvious hierarchy: title > primary state/action > content > metadata/help.

## Layout and transient UI
- Choose overlay versus inline layout from the interaction semantics and the current product pattern; do not hard-code one presentation for every menu or autocomplete.
- Transient UI must not cause unintended layout shifts. If movement is intentional, it should be part of the interaction design rather than an implementation side effect.
- Preserve existing page-state boundaries instead of rendering irrelevant UI shells and hiding their contents.
- On mobile, preserve intentional side gutters and verify that broad width rules do not accidentally cancel centered max-width layouts.
- Match nearby control sizing and density instead of introducing disproportionately large secondary controls.

## Interaction states
- Make the primary path and alternatives visually understandable using the product's existing hierarchy.
- Destructive and close actions should follow the established local pattern.
- Do not expose internal identifiers, hashes, transport handles, or namespaced IDs in user-visible UI.

## Visual consistency checklist
Before declaring UI done, compare against existing Duel UI:
- font size/family/weight/line-height
- border thickness and shadow depth
- button height/padding
- spacing rhythm
- color hierarchy
- mobile side gutters
- no unintended layout shift
- overlay z-index/clipping
- long names/IDs wrapping/truncation

## Verification
1. Syntax/tests are required but are not visual proof.
2. Inspect relevant rendered state at phone width and desktop width.
3. Verify hidden/visible transitions such as waiting -> playing and menu closed -> open.
4. If a reference screenshot exists, compare interaction geometry: what overlays what, what moves, font hierarchy, relative scale.
5. Do not stack successive CSS hotfix blocks. Consolidate superseded rules before finishing.
