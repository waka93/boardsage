# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BoardSage is a board game encyclopedia that lets users query rulebooks in plain English, powered by Claude AI. It is currently a greenfield project — no stack, build system, or source files exist yet beyond this file and the README.

## Architecture (to be decided)

When implementing, the core data flow will be:
1. **Ingest** — parse and chunk PDF/text rulebooks
2. **Embed** — generate vector embeddings for semantic search
3. **Retrieve** — find relevant rulebook passages for a user query
4. **Generate** — pass retrieved context to Claude to produce a plain-English answer

Key architectural choices to make before writing code:
- Frontend framework (if any)
- Backend language and web framework
- Vector store (e.g., pgvector, Chroma, Pinecone)
- Claude model and whether to use prompt caching for large rulebook contexts

## Claude API Usage

This project uses the Anthropic SDK. When integrating Claude:
- Use prompt caching for rulebook context passed in system or user turns — rulebooks can be large and repeated across queries
- Default to `claude-sonnet-4-6` unless reasoning depth warrants `claude-opus-4-7`
- Stream responses for the query interface to reduce perceived latency
