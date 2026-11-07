import random
wins=0
losses=0
rolls=100
for i in range(rolls):
    roll=random.randint(1, 6)
    roll2=random.randint(1, 6)
    roll3=random.randint(1, 6)
    roll4=random.randint(1, 6)
    if roll==1 or roll2==1:
        wins+=1
    elif roll!=1 and roll2!=1:
        losses+=1
    else:
        print("Error")
    #print(roll)
print(wins,losses)
print(wins+losses)
print(str(wins/rolls*100)+"%")