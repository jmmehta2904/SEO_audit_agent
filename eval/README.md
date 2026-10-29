# Saved-page evaluation

Run `python eval/run_eval.py` from the project root. This path uses only local
HTML, extraction, and deterministic rules; it does not initialize a model or
fetch websites. Exit 0 means exact agreement, 1 means a rule mismatch, and 2
means invalid configuration or an unavailable live review.

The ten synthetic, saved pages have manually assigned expected identifiers in
`expected.json`. The complete page has no expected issues. Other cases isolate
missing/short/long titles, missing/short descriptions, multiple H1s, absent image
alternatives (with valid decorative and descriptive controls), missing technical
metadata, and hostile sparse content. The empty page covers absent H1 and body
text. All 12 implemented rule identifiers occur at least once.

These are regression fixtures, not a representative web sample or evidence that
the heuristics predict rankings. Repeated editorial paragraphs keep the positive
controls above the word threshold; length boundary tests remain in `tests/`.
Precision = TP/(TP+FP), recall = TP/(TP+FN); identifiers are compared per page.
A zero denominator is N/A. Total precision and recall use summed TP/FP/FN
(micro-averaging), rather than averages of per-page percentages.

`python eval/run_eval.py --live` additionally sends each saved page's bounded
facts and body excerpt to the ADK Gemini adapter through Vertex ADC. It uses the
production prompt, Audit schema and evidence verifier, with one attempt and a
45-second deadline per page. It does not run the website-fetching workflow or
fetch the example URLs. Set MODEL_NAME, GOOGLE_CLOUD_PROJECT and
GOOGLE_CLOUD_LOCATION; unset API keys and disable DEV_STUB_MODEL. There is no
need for an ACCESS_CODE or CONTACT_URL because no HTTP service or crawler runs.
This command is explicitly opt-in and potentially billable. It prints proposed,
verified and dropped counts, and marks failures unavailable; totals exclude
failed reviews and report the completed denominator. Verification only establishes
an evidence match, not correctness of a subjective judgment. Workflow integration
and retries are covered separately by offline tests.

The checked-in `RESULTS.md` contains the actual offline output. Live Vertex
results have not been recorded.
