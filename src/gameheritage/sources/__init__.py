"""Clients for the outside sources: Wikidata, Wikimedia Commons, Wikipedia, Europeana, Fandom."""

from .http import HttpClient, HttpError

__all__ = ["HttpClient", "HttpError"]
