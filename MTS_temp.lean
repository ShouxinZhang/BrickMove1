import Mathlib

section ConjugatcyCoset
private lemma p_pow_m_totient (p m : ℕ) (hm2 : p.Coprime m) : (p : ZMod m) ^ m.totient = 1 := by
  let pu : (ZMod m)ˣ := ZMod.unitOfCoprime p hm2
  have : pu ^ m.totient = 1 := by
    apply ZMod.pow_totient
  rw [show (p : ZMod m) ^ m.totient = pu ^ m.totient from rfl]
  -- do some `Units` conversion
  apply_fun Units.val at this
  simp only [Units.val_one, Units.val_pow_eq_pow_val] at this
  exact this
variable (p : ℕ) [pp : Fact <| Nat.Prime p] (m : ℕ) (hm1 : m > 1) (hm2 : p.Coprime m)

private def conjugacy_relation (a b : ZMod m) : Prop := ∃ k : ℕ, a = (p ^ k) • b


/--
  The conjugacy relation is decidable.
-/
def decidable_rel : DecidableRel (conjugacy_relation p m) := by
  intro a b
  -- just need to check $k < \phi(m)$
  apply decidable_of_iff (∃ k : (Fin m.totient), a = ((p : ZMod m) ^ k.val) * b)
  simp [conjugacy_relation]
  constructor <;> intro h <;> let ⟨k, hk⟩ := h
  · use k.val
  · use ⟨k % (m.totient), by
      apply Nat.mod_lt
      apply Nat.totient_pos.mpr
      exact Nat.zero_lt_of_lt hm1⟩
    -- use $k % \phi(m)$ in Fin m.totient, show that they are equal
    rw [hk]
    simp
    nth_rw 1 [<-Nat.div_add_mod k m.totient]
    rw [pow_add, pow_mul]
    simp [p_pow_m_totient p m hm2]

/--
  Divide `ZMod m` into conjugacy classes.
  We use `Finpartition` to guarantee the results are computable.
-/
def conjugacy_coset :=
  letI _ : NeZero m := NeZero.of_gt hm1
  letI _ := decidable_rel p m hm1 hm2
  Finpartition.ofSetoid (
    Setoid.mk (conjugacy_relation p m) (by
      -- prove that it is an equivalence relation
      unfold conjugacy_relation
      constructor
      · -- rel
        intro _
        use 0
        simp
      · -- sym
        intro x y ⟨k, hxy⟩
        rw [hxy]
        simp only [smul_smul, <-pow_add]
        use (k * (m.totient) - k) -- just find an inverse bigger enough
        rw [Nat.sub_add_cancel, mul_comm, pow_mul]
        simp
        rw [p_pow_m_totient]
        simp
        exact hm2
        -- show that it is indeed bigger enough
        refine Nat.le_mul_of_pos_right k ?h.h
        refine Nat.totient_pos.mpr ?h.h.a
        omega
      · -- trans
        intro _ _ _ ⟨k, hxy⟩ ⟨l, hyz⟩
        use (k + l)
        rw [hxy, hyz]
        rw [smul_smul, <-pow_add]
    )
  )

/--
  Finally, we can constuct the following equivalence:
-/
def final_relation (alpha : GaloisField p n) (i j : ZMod m)  := minpoly (ZMod p) (alpha ^ i.val) = minpoly (ZMod p) (alpha ^ j.val)

/--
  Actually, this relation is equivalent to the relation in the conjugacy coset.
-/
theorem final_relation_iff_in_the_same_conjugacy_class
    {n : ℕ} (hn : n > m) (alpha : GaloisField p n) (halpha : orderOf alpha = m)
    (i j : ZMod m) :
    final_relation p m (n := n) alpha i j ↔ i ∈ (conjugacy_coset p m hm1 hm2).part j := by
  sorry

end ConjugatcyCoset
