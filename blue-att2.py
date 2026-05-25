import ollama

client=ollama.Client()

model = "mistral-nemo:latest"
prompt = "Hello"

response = client.generate(model=model, prompt=prompt)

print("Blue Onion: ")
print(response.response)