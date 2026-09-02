from dynamic_cypher_engine import ask_question


question = input("Enter your question: ")

result = ask_question(question)

print("\nFinal Answer:")
print(result["answer"])