#include <iostream>
#include <vector>

int main() {
    int n;
    std::cin >> n;
    const int coins[] = {1, 7, 10, 25};
    std::vector<int> fewest(n + 1, 1 << 30), last(n + 1, 0);
    fewest[0] = 0;
    for (int amount = 1; amount <= n; ++amount) {
        for (int coin : coins) {
            if (coin <= amount && fewest[amount - coin] + 1 < fewest[amount]) {
                fewest[amount] = fewest[amount - coin] + 1;
                last[amount] = coin;
            }
        }
    }
    for (int amount = n; amount > 0; amount -= last[amount]) {
        std::cout << last[amount] << (amount == last[amount] ? "\n" : " ");
    }
}
