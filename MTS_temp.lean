import Mathlib

/--
The ring of polynomials $\mathbb{Q}[X]$ with rational coefficients is a Noetherian ring.
This follows from Hilbert's Basis Theorem, given that $\mathbb{Q}$ (as a field) is Noetherian.
-/
theorem Q_X_is_Noetherian : IsNoetherianRing (Polynomial ℚ) := by
  sorry
