import org.junit.jupiter.api.Assertions.assertEquals

fun sum(numbers: List<Long>): Long = numbers.sum()

fun main() {
    assertEquals(10L, sum(listOf(1L, 2L, 3L, 4L)))
    println(sum(readln().trim().split(" ").map(String::toLong)))
}
