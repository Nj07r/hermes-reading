# Reading is installed

Next, connect Gmail (once, about 3 minutes):

1. Create a free Google OAuth client of type **Desktop app** and download its JSON.
   The steps: `hermes reading setup` prints them, and the Reading page shows them too.
2. Run `hermes reading setup --client ~/Downloads/client_secret_....json`.
   It saves the client and opens the Google sign-in. Hermes keeps the token, not Reading.
3. Pick your newsletters. In Hermes Desktop, open **Reading** in the sidebar
   (switch the plugin on in Settings > Plugins first).
