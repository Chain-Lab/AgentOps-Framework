/* Policy Console Lite — minimal JS (Phase 26) */

document.addEventListener('DOMContentLoaded', () => {
  // Auto-submit filter form on select change (future-proofing)
  const filterForm = document.querySelector('.filter-form');
  if (filterForm) {
    filterForm.querySelectorAll('select').forEach(sel => {
      sel.addEventListener('change', () => filterForm.submit());
    });
  }
});
