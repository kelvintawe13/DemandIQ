# Notebooks

**The notebook used for the DemandIQ deliverable is
[`DemandIQ_03_Training_and_Export_final.ipynb`](DemandIQ_03_Training_and_Export_final.ipynb).**

It is the only notebook that runs the path the backend consumes, end to end:
config → install → data → Section 3.7.1 audit → exploration → all training
stages → evaluation → SHAP → sensitivity → rolling-origin backtest → export of
the `serving_bundle.joblib` (validated against the backend's bundle contract).

| Notebook | Status | Purpose |
| --- | --- | --- |
| `DemandIQ_03_Training_and_Export_final.ipynb` | **Canonical** | the run and export to use |
| `DemandIQ-03-Training-and-Export.ipynb` | Superseded | earlier draft of the same flow |
| `DemandIQ-01-Data-and-Baseline.ipynb` | Reference | data and baseline narrative figures |
| `DemandIQ-02-Results.ipynb` | Reference | Chapter 4 tables and figures from a run |

Do not run the superseded or reference notebooks for the deliverable. They can
write artifacts under different paths, and their numbers are not the ones the
serving bundle publishes.
