"""Automated red-teaming: seeded attack generation, a runner that scores attacks with the eval
helpers, and a regression set built from every attack that ever got through.

    python -m evals.redteam.run --seed 1                      # generate + run a batch offline
    python -m evals.redteam.run --cases evals/cases_redteam_regressions.yaml --gate
"""
