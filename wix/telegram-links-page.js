import wixData from 'wix-data';

const COLLECTION_ID = 'TelegramLinks';

const BUTTONS = {
  landing: '#mainCtaButton',
  thailand: '#thailandCtaButton',
  vietnam: '#vietnamCtaButton',
};

$w.onReady(async function () {
  try {
    const results = await wixData.query(COLLECTION_ID).find();
    const links = Object.fromEntries(
      results.items
        .filter((item) => item.linkType && item.url)
        .map((item) => [item.linkType, item.url])
    );

    Object.entries(BUTTONS).forEach(([linkType, elementId]) => {
      const url = links[linkType];
      if (url && $w(elementId)) {
        $w(elementId).link = url;
      }
    });
  } catch (error) {
    console.error('Failed to load TelegramLinks from Wix CMS', error);
  }
});
