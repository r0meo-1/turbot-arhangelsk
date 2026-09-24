# VK Clips Creative Guide — TurBot

Status: project production guideline for vertical 9:16 creatives.

## 1. Source hierarchy

Use this order when rules disagree:

1. VK's own current creative/ad guidance.
2. Current mobile-device preview in the target VK placement.
3. This project guide.
4. Third-party safe-zone references only as conservative hints.

VK's official vertical-video deck states that important content should not be placed in the top 10% or bottom 20% of the player because mandatory interface elements occupy those areas. The same deck specifies 9:16, up to 60 seconds, up to 90 MB, and H.264/AAC support for the referenced clips placement.

Official reference:
- https://object2-ac.vk-apps.com/vk-education-0554ff56-27eb-4093-a702-685722cef6d9/media/1b/e61bb0d4cb524a159acf47a0051b44d6.pdf
- VK Clips rules: https://vk.com/@vkclips-pravila-vk-klipov

## 2. TurBot master format

Project master:
- canvas: **1080 × 1920**
- aspect ratio: **9:16**
- preferred duration for performance creatives: **20–30 s**
- video: H.264
- audio: AAC
- export: MP4
- one primary CTA per creative
- no fake VK UI controls inside the artwork

## 3. Safe zones

### Official hard exclusion

On a 1080 × 1920 project canvas:

- top 10%: **192 px** — no hook, CTA, face, price, destination, logo or legal text
- bottom 20%: **384 px** — no critical information

This leaves the official vertical safe band from **y=192** through **y=1536**.

### TurBot conservative side guards

VK's official material above does not define a fixed left/right pixel rail for every Clips UI variant. The action rail, author/caption UI, device chrome and expanded descriptions can shift by placement and app version.

For TurBot production, use these additional project guards:

- left: **108 px (10%)**
- right: **216 px (20%)**

These side values are **not claimed as VK official constants**. They intentionally reserve extra room for organic Clips controls and cross-placement variation.

Critical-content box:

- x: **108 → 864**
- y: **192 → 1536**
- size: **756 × 1344**

Import `docs/assets/vk_clips_safe_zone.svg` into Figma or the editor as a non-exporting overlay.

### Placement rules

- Hook/headline: upper-middle area, roughly y=300–800.
- Main face/product: center-left or center, never pinned to the right edge.
- Subtitles: inside the critical-content box; keep 24–48 px internal padding from its boundary.
- CTA/end card: preferably y=1080–1450, not in the bottom 384 px.
- Brand mark: small and secondary. Do not rely on a corner watermark for recognition.
- Keep the right side visually quiet enough that reactions/share controls can sit above the footage without hiding meaning.

Exact author-row height and action-button coordinates are treated as **runtime UI**, not as a design contract. Always preview on a real VK mobile client before publish.

## 4. User path from Clip to TurBot

Use one measurable path per creative:

1. Viewer watches the clip.
2. CTA sends the user into the VK campaign entry point.
3. The campaign source is encoded in the existing `?ref=` tag.
4. TurBot preserves the tag through the lead.
5. Manager handoff and admin export retain the same source tag.

Campaign tags already used by the performance workflow:

- `video_pain`
- `video_dream`
- `video_vs`
- organic lane: `vk_post_pain`, `vk_post_dream`, `vk_post_battle`

Creative copy should describe the action, not print a raw tracking URL in the video.

## 5. Visual system

Base direction:
- travel-first imagery, not generic SaaS dashboards
- cyan/sky accents with bright neutral typography
- natural tropical daylight for destination scenes
- high local contrast behind captions
- one focal subject per shot
- minimal decorative clutter
- no fake booking confirmation, fake price, fake availability or fake customer chat

Consistency rules:
- same visual identity across Pain / Dream / Battle
- keep key subject scale similar between variants
- use background/scene and copy angle to distinguish variants, not a completely different brand style
- generated images must not contain baked-in text; add typography in the editor

## 6. Prompt library

The prompts below are intentionally text-free. Add copy later in Figma/video editing so the safe zone stays controllable.

### Pain angle

```text
Vertical 9:16 travel advertising scene for a Russian travel assistant brand.
A tired traveler at home comparing too many travel tabs and offers, visually overwhelmed but realistic, not comedic.
Smartphone and laptop visible, screens abstract and unreadable, no logos, no baked-in text.
Subject positioned center-left, preserve a clean empty rail on the right and clear space in the upper-middle for a headline.
Cinematic natural indoor lighting, crisp realistic photography, cyan and sky-blue accent details, premium but approachable, no watermark, no UI imitation.
```

### Dream angle

```text
Vertical 9:16 aspirational travel scene for a modern travel assistant.
A relaxed traveler arriving at a bright tropical beach resort, warm natural daylight, turquoise water, clean premium atmosphere, realistic documentary photography.
Subject center-left with breathing room around the face, preserve clean negative space in the upper-middle for headline text and keep the right edge visually quiet for mobile interface controls.
Cyan and sky-blue brand accents, believable travel details, no baked-in text, no logos, no watermark, no fake price or booking confirmation.
```

### Battle / versus angle

```text
Vertical 9:16 split-concept travel advertising image showing contrast between chaotic manual tour searching and a calm assisted travel-planning experience.
Left side: cluttered browsing, many abstract tabs, indecision.
Right side: clean simple travel-planning moment leading to a real vacation mood.
Do not render readable website text or interface controls.
Keep the central upper area readable for a short headline, keep the far-right edge uncluttered for platform controls, realistic lighting, coherent cyan and sky-blue accents, premium photorealistic style.
```

### TurBot assistant character

```text
Friendly modern digital travel assistant character for a Russian travel brand, human-centered rather than robotic, confident and helpful, contemporary travel styling, cyan and sky-blue accents, clean silhouette, realistic premium illustration.
Vertical 9:16 composition, character center-left, generous negative space in upper-middle for copy, quiet right edge for mobile action controls, no text, no watermark, no fake application UI.
```

## 7. Production workflow

1. **Brief**
   - select Pain / Dream / Battle
   - bind exactly one source tag
   - write one measurable CTA

2. **Generate concept**
   - use the matching prompt
   - reject outputs with baked-in text, broken hands/faces, fake UI or unverifiable claims

3. **Concept approval**
   - approve composition and subject only
   - do not approve typography at the generation stage

4. **1080×1920 assembly**
   - place concept in the project master
   - apply `vk_clips_safe_zone.svg` as an overlay

5. **Typography/subtitles**
   - place all critical text in the critical-content box
   - keep the right control rail clear
   - keep final CTA above the bottom hard exclusion zone

6. **Tracking**
   - apply the intended `?ref=` source tag to the CTA destination
   - do not change the tag during export/publishing

7. **Export**
   - MP4 / H.264 / AAC
   - verify 9:16
   - inspect compression and audio

8. **Mobile QA**
   - preview in the actual VK mobile placement
   - inspect top bar, author/caption area, right-side controls and expanded description
   - verify the first 2 seconds still communicate the hook

9. **Acquisition smoke**
   - open the final CTA
   - ensure TurBot receives the expected source tag
   - release only after the relevant attribution gate is green

## 8. Pre-production checklist

- [ ] 1080×1920 / 9:16.
- [ ] Hook readable in the first 2 seconds.
- [ ] No critical content in top 10%.
- [ ] No critical content in bottom 20%.
- [ ] Critical text inside the TurBot conservative box.
- [ ] Right-side control rail does not cover meaning.
- [ ] Subtitles are readable without sound.
- [ ] No baked-in generated text.
- [ ] No fake VK UI.
- [ ] No unverified price/availability claims.
- [ ] One primary CTA.
- [ ] Correct campaign `?ref=` tag.
- [ ] H.264/AAC export checked.
- [ ] Real-device VK preview completed.
- [ ] Final CTA opens the intended TurBot flow.
- [ ] Attribution smoke/gate status checked before paid spend.

## 9. Handoff state

Canonical handoff:

```text
Prompt
  -> generated concept
  -> concept approval
  -> 1080x1920 assembly
  -> safe-zone overlay
  -> copy/subtitles/CTA
  -> mobile QA
  -> source-tag smoke
  -> final production creative
```

Do not move a creative to production merely because the render looks good in the editor. The live VK overlay and the source-tag path are part of acceptance.
