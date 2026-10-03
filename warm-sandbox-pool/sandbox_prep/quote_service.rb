#!/usr/bin/env ruby
# frozen_string_literal: true

# Demo Ruby/Sinatra service for warm sandbox pool example
# A simple "quote of the day" API that runs in each initialized sandbox

require 'sinatra'
require 'json'

set :port, 4567
set :bind, '0.0.0.0'

QUOTES = [
  { author: 'Alan Kay', text: 'The best way to predict the future is to invent it.' },
  { author: 'Donald Knuth', text: 'Premature optimization is the root of all evil.' },
  { author: 'Rob Pike', text: 'Simplicity is complicated.' },
  { author: 'Ken Thompson', text: 'When in doubt, use brute force.' },
  { author: 'Yukihiro Matsumoto', text: 'Ruby is designed to make programmers happy.' },
  { author: 'David Heinemeier Hansson', text: 'Convention over configuration.' },
  { author: 'Sandi Metz', text: 'Duplication is far cheaper than the wrong abstraction.' },
  { author: 'Martin Fowler', text: 'Any fool can write code that a computer can understand. Good programmers write code that humans can understand.' }
].freeze

# Health check endpoint
get '/health' do
  content_type :json
  { status: 'ok', service: 'quote_service', version: '1.0.0' }.to_json
end

# Get a random quote
get '/quote' do
  content_type :json
  quote = QUOTES.sample
  {
    quote: quote[:text],
    author: quote[:author],
    timestamp: Time.now.iso8601
  }.to_json
end

# Get all quotes
get '/quotes' do
  content_type :json
  { quotes: QUOTES, count: QUOTES.size }.to_json
end

# Get quote by author (case-insensitive partial match)
get '/quotes/:author' do
  content_type :json
  author_param = params['author'].downcase
  matches = QUOTES.select { |q| q[:author].downcase.include?(author_param) }

  if matches.empty?
    status 404
    { error: 'No quotes found for that author' }.to_json
  else
    { quotes: matches, count: matches.size }.to_json
  end
end

# Info endpoint
get '/' do
  content_type :json
  {
    service: 'Quote of the Day API',
    version: '1.0.0',
    endpoints: {
      health: 'GET /health',
      random_quote: 'GET /quote',
      all_quotes: 'GET /quotes',
      by_author: 'GET /quotes/:author'
    },
    sandbox_id: ENV['SANDBOX_ID'] || 'unknown'
  }.to_json
end

# Startup message
puts "🚀 Quote Service starting on port #{settings.port}"
puts "📚 Loaded #{QUOTES.size} quotes"
puts "🎯 Sandbox ID: #{ENV['SANDBOX_ID'] || 'not set'}"
