/* Submit a Task: show what the chosen service will use from the plan, as soon as it's picked.
   The texts come from the server (#planPreviews); the server checks the allowance again later. */
(function () {
  var data = document.getElementById('planPreviews'), select = document.getElementById('id_service_version');
  var box = document.getElementById('planPreview'), text = document.getElementById('planPreviewText');
  if (!data || !select || !box || !text) return;
  var previews = {};
  try { previews = JSON.parse(data.textContent); } catch (e) { return; }
  function show() {
    var t = previews[select.value];
    if (t) { text.textContent = t; box.hidden = false; } else { box.hidden = true; }
  }
  select.addEventListener('change', show);
  show();
})();
