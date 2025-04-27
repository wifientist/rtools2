from bcrypt import hashpw, gensalt

password = input('enter password to hash:')
hashed_password = hashpw(password.encode(), gensalt()).decode()

print(hashed_password)  # Store this in the database
