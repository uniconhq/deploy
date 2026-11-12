#include <iostream>
#include <unordered_map>
#include <vector>

int main() {
    int n;
    long long k;
    std::cin >> n >> k;
    std::vector<long long> numbers(n);
    for (auto &x : numbers) std::cin >> x;
    std::unordered_map<long long, int> seen;
    for (int j = 0; j < n; j++) {
        auto found = seen.find(k - numbers[j]);
        if (found != seen.end()) {
            std::cout << found->second + 1 << ' ' << j + 1 << '\n';
            return 0;
        }
        seen.emplace(numbers[j], j);
    }
}
