export const config = {
  apiBase: process.env.NEXT_PUBLIC_API_BASE ?? '',
  cognitoDomain: process.env.NEXT_PUBLIC_COGNITO_DOMAIN ?? '',
  clientId: process.env.NEXT_PUBLIC_CLIENT_ID ?? '',
  redirectUri: process.env.NEXT_PUBLIC_REDIRECT_URI ?? '',
};
