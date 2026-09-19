(() => {
  const SAFE_TAG = /^[A-Za-z0-9_-]{1,64}$/;
  const params = new URLSearchParams(window.location.search);
  const candidate =
    params.get('src') ||
    params.get('source_tag') ||
    params.get('utm_content') ||
    params.get('utm_campaign') ||
    'landing';
  const sourceTag = SAFE_TAG.test(candidate) ? candidate.toLowerCase() : 'landing';
  const botUrl = `https://t.me/apreltour_bot?start=${encodeURIComponent(sourceTag)}`;
  const vkUrl = `https://vk.me/club240310110?ref=${encodeURIComponent(sourceTag)}`;

  document.querySelectorAll('[data-turbot-link]').forEach((link) => {
    link.href = botUrl;
  });
  document.querySelectorAll('[data-vk-turbot-link]').forEach((link) => {
    link.href = vkUrl;
  });
})();
