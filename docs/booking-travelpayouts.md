# VK Mini App → Booking.com via Travelpayouts

The VK Mini App can open a monetized Booking.com hotel search using the trip
parameters already entered by the user.

## Flow

1. User enters destination, check-in date, nights and party in the VK Mini App.
2. The signed VK launch params and trip payload are POSTed to
   `/vk/miniapp/booking-link`.
3. The backend validates the VK signature and the trip payload.
4. The backend builds a full Booking.com search URL.
5. Travelpayouts Partner Links API converts that URL to a project affiliate
   link (`sub_id=vk_booking_search`).
6. The Mini App opens the returned Booking.com link.

No Travelpayouts token is exposed to the browser.

## Production environment

Add these values to `/opt/turbot/.env` on the production server:

```env
TRAVELPAYOUTS_API_TOKEN="..."
TRAVELPAYOUTS_MARKER=778488
TRAVELPAYOUTS_TRS=574782
```

`TRAVELPAYOUTS_API_TOKEN` is available in Travelpayouts → Profile → API token.
Do not commit it to GitHub and do not put it in VK Mini App JavaScript.

The marker and project id are non-secret defaults in the code, but keeping them
in `.env` makes staging/production separation explicit.

## Travelpayouts requirement

The `R0meo1` project must have access to the Booking.com affiliate program.
If the program is still under review or unavailable, Travelpayouts will not
create the Booking.com partner link and the Mini App will show a temporary
"not connected" message.

## What this integration does not do

This is a monetized deep-link search, not a hotel inventory API. The bot does
not scrape Booking.com and does not display Booking.com room prices inside VK.
For native hotel cards/prices inside the bot, a separate approved hotel API is
required.
