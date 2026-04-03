###############################################################################
#                                                                             #
#   PIPELINE ORCHESTRATOR                                                     #
#                                                                             #
###############################################################################

from __future__ import annotations

from services.pipeline.step1_ingestion import run_step1_data_ingestion
from services.pipeline.step2_correlations import run_step2_correlations
from services.pipeline.step3_stationarity import run_step3_stationarity
from services.pipeline.step4_decomposition import run_step4_decomposition
from services.pipeline.step5_training import run_step5_training
from services.pipeline.step6_evaluation import run_step6_evaluation

if __name__ == "__main__":

    print("█" * 75)
    print("   XoCompass v10.1 — 6-Step Modular SARIMAX Pipeline")
    print("   Econometric Fixes: Hangover | No-Log | Unlocked GridSearch")
    print("█" * 75)

    # ── Step 1: Data Ingestion ──
    step1 = run_step1_data_ingestion("data/KJS Data.csv")

    # ── Step 2: Correlations ──
    step2 = run_step2_correlations(step1)

    # ── Step 3: Stationarity ──
    step3 = run_step3_stationarity(step1)

    # ── Step 4: Decomposition ──
    step4 = run_step4_decomposition(step1)

    # ── Step 5: Training ──
    step5 = run_step5_training(step1, step3)

    # ── Step 6: Evaluation ──
    step6 = run_step6_evaluation(step1, step3, step5)