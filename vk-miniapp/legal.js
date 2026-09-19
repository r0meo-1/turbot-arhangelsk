(async () => {
  const fallback = {
    operatorName: 'ТА «АПРЕЛЬ тур»',
    privacyContact: 'Наталья Ильина, +7 902 193-29-23, VK: https://vk.ru/id112655584',
    projectUrl: 'https://r0meo1.ru/apreltour/'
  };
  let config = fallback;
  try {
    const response = await fetch('./legal.json', { credentials: 'same-origin', cache: 'no-store' });
    if (response.ok) config = { ...fallback, ...(await response.json()) };
  } catch (_) {}
  document.querySelectorAll('[data-legal-operator]').forEach((node) => {
    node.textContent = config.operatorName || fallback.operatorName;
  });
  document.querySelectorAll('[data-legal-contact]').forEach((node) => {
    node.textContent = config.privacyContact || fallback.privacyContact;
  });
  document.querySelectorAll('[data-project-url]').forEach((node) => {
    node.href = config.projectUrl || fallback.projectUrl;
  });
})();