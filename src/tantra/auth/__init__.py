"""
Tantra AI — Authentication layer
fastapi-users: email/password + Google OAuth + GitHub OAuth + JWT refresh tokens

Supports:
  POST /auth/register           — email + password signup
  POST /auth/login              — JWT access + refresh token
  POST /auth/refresh            — refresh access token
  GET  /auth/google             — Google OAuth redirect
  GET  /auth/google/callback    — Google OAuth callback
  GET  /auth/github             — GitHub OAuth redirect
  GET  /auth/github/callback    — GitHub OAuth callback
  GET  /users/me                — current user profile
  PATCH /users/me               — update profile
"""
