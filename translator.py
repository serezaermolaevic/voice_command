from deep_translator import GoogleTranslator
frost = str(input().islower())
to = str(input().islower())
word = str(input())
print(GoogleTranslator(source='English', target='Russian').translate(input()))