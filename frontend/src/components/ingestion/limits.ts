/**
 * Ingestion limits, mirrored from the backend.
 *
 * The server is the authority: `normaliser.validate_demand_qty` refuses a
 * quantity above `MAX_DEMAND_QTY` with a 422 naming the field. These copies
 * exist so the interactive form can say so *while the number is being typed*,
 * rather than after a round trip that already looked like it worked.
 *
 * That distinction is not cosmetic. A single entry of 222,000,100 was once
 * accepted and stored, and then failed every later forecast for that location
 * with a 500 — nothing the operator typed looked wrong until an unrelated
 * screen broke. The failure was three taps away from its cause.
 *
 * Only the manual form mirrors the cap. Bulk paths (CSV/XLSX, POS, image OCR)
 * deliberately let the server answer: they report per-row errors already, and a
 * file is validated row by row on purpose rather than rejected wholesale.
 *
 * Keep this in step with `backend/app/services/ingestion/normaliser.py`.
 */
export const MAX_DEMAND_QTY = 10_000_000;

/** The message the form shows, worded like the server's own refusal. */
export const MAX_DEMAND_QTY_MESSAGE = `Demand cannot exceed ${MAX_DEMAND_QTY.toLocaleString('en-US')} visitors in a day`;
